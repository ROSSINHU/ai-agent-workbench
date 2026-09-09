# -*- coding: utf-8 -*-
"""工作台核心逻辑层（headless，零 tkinter 依赖）。

职责：便携配置解析、npm/Node 环境探测、系统代理、子进程环境构造、
服务定义、用户设置持久化、结构化日志、服务进程状态。
本层不依赖任何 GUI，可被命令行、测试或未来的其他界面复用。
"""
import os
import re
import json
import glob
import time
import shutil
import socket
import threading
import subprocess

from workbench.platform_windows import read_system_proxy as _registry_proxy

APPDATA = os.environ.get("APPDATA", "")
NO_WINDOW = subprocess.CREATE_NO_WINDOW
VERSION_RE = re.compile(r"v?(\d+(?:\.\d+)+)")

# ---------------- 便携配置覆盖（实现跨机器通用性） ----------------
# 由 AI agent管理工作台.bat 探测后写入 workbench2.cfg 或设置 WORKBENCH_* 环境变量；
# 优先级：环境变量 > workbench_settings.json（界面内切换记录）> workbench2.cfg
# > 代码内默认值。空字符串表示强制走 bat 向导或自动探测，兼容 32 位安装。
_WORK_DIR_DEFAULT = ""  # 空：首次运行必走 bat 向导或 UI 切换
_SYSTEM_NPM_DEFAULT = ""  # 空：强制走 _npm_search_dirs() 自动探测
_FNM_NPM_DEFAULT = os.path.join(APPDATA, "fnm", "aliases", "default", "npm.cmd") if APPDATA else ""


def _app_root():
    """应用根目录（配置文件与 logs 的锚点）。

    - 源码运行：core.py 位于 <root>/workbench/core.py，根目录是其上两级；
    - PyInstaller 打包：sys.frozen=True，根目录取 exe 所在目录
      （--onefile 时是引导解包目录的上一层，即 exe 旁）。
    配置（workbench2.cfg / workbench_settings.json）与日志都落在根目录，
    保证「拷贝整个目录即可便携迁移」以及打包后相对 exe 读写。
    """
    if getattr(__import__("sys"), "frozen", False):
        return os.path.dirname(os.path.abspath(__import__("sys").executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


APP_ROOT = _app_root()
SETTINGS_PATH = os.path.join(APP_ROOT, "workbench_settings.json")
_CFG_PATH = os.path.join(APP_ROOT, "workbench2.cfg")


def _read_text_dual(path):
    """双编码读取文本：utf-8 优先，失败回退 gb18030（bat 以系统码页写 cfg）。

    bat 的 `>` 重定向按系统码页（中文 Windows=GBK）落盘，Python 端写文件用
    utf-8；两种来源都可能存在，依次尝试即可正确解码（纯 ASCII 两者等价）。
    返回文本；读取失败返回 None。
    """
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return None
    if raw.startswith(b"\xef\xbb\xbf"):  # UTF-8 BOM（bat 解析不了，Python 侧容忍）
        return raw.decode("utf-8-sig", errors="replace")
    for enc in ("utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _load_portable_overrides():
    """读取同目录 workbench2.cfg（KEY=VALUE），返回 dict。"""
    text = _read_text_dual(_CFG_PATH)
    cfg = {}
    if not text:
        return cfg
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        cfg[k.strip()] = v.strip()
    return cfg


_PORTABLE_CFG = _load_portable_overrides()


def _settings_workdir():
    """读取 workbench_settings.json 里记录的工作目录（界面内切换写入）；无则 None。"""
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return str(data.get("workdir") or "").strip() or None
    except (OSError, ValueError):
        pass
    return None


def _override(env_key, cfg_key, default):
    """环境变量 > cfg > 默认值。"""
    v = os.environ.get(env_key, "").strip()
    if v:
        return v
    v = _PORTABLE_CFG.get(cfg_key, "").strip()
    if v:
        return v
    return default


def _fnm_bases():
    """fnm 根目录候选：FNM_DIR 环境变量 > %APPDATA%\\fnm > %LOCALAPPDATA%\\fnm。"""
    bases = []
    fd = os.environ.get("FNM_DIR", "").strip()
    if fd:
        bases.append(fd)
    for v in ("APPDATA", "LOCALAPPDATA"):
        val = os.environ.get(v, "").strip()
        if val:
            b = os.path.join(val, "fnm")
            if b not in bases:
                bases.append(b)
    return bases


def _npm_search_dirs():
    """npm.cmd 候选安装目录（覆盖 64/32 位、全机/按用户、nvm 安装方式）。"""
    dirs = []
    pf = os.environ.get("ProgramFiles") or r"C:\Program Files"
    pf86 = os.environ.get("ProgramFiles(x86)")  # 32 位 Node 常装在 x86 目录
    for base in (pf, pf86):
        if base:
            dirs.append(os.path.join(base, "nodejs"))
    la = os.environ.get("LOCALAPPDATA", "").strip()
    if la:
        dirs.append(os.path.join(la, "Programs", "nodejs"))
    ns = os.environ.get("NVM_SYMLINK", "").strip()
    if ns:
        dirs.append(ns)
    return dirs


def _resolve_npm(preferred):
    """确保系统 npm 可用：配置路径有效则用之，否则自动探测。

    探测顺序（标准安装位置优先，避免会话注入的 PATH 命中异常 npm）：
    标准安装目录（含 32 位 x86 与按用户安装）→ fnm 默认别名 → PATH。
    全部失败时返回 (配置原值, "未找到")，保留原值用于报错提示。
    """
    if preferred and os.path.isfile(preferred):
        return preferred, "配置路径"
    for d in _npm_search_dirs():
        cand = os.path.join(d, "npm.cmd")
        if os.path.isfile(cand):
            return cand, f"自动探测（{d}）"
    for base in _fnm_bases():  # 仅用 fnm、无系统 Node 的机器
        cand = os.path.join(base, "aliases", "default", "npm.cmd")
        if os.path.isfile(cand):
            return cand, f"自动探测（fnm 默认别名：{base}）"
    hit = shutil.which("npm.cmd") or shutil.which("npm")
    if hit:
        return hit, "PATH"
    return (preferred or ""), "未找到"


def _resolve_fnm_npm(preferred, system_npm):
    """解析 pi 使用的 npm：fnm 默认别名优先，无 fnm 的机器回退系统 npm。"""
    if preferred and os.path.isfile(preferred):
        return preferred, "配置路径"
    for base in _fnm_bases():
        cand = os.path.join(base, "aliases", "default", "npm.cmd")
        if os.path.isfile(cand):
            return cand, f"自动探测（fnm：{base}）"
    if system_npm and os.path.isfile(system_npm):
        return system_npm, "回退系统 npm（未检测到 fnm）"
    return (preferred or ""), "未找到"


def _resolve_work_dir():
    """工作目录解析：env（bat 显式传入）> settings（界面内切换）> cfg（bat 向导）> 默认。"""
    v = os.environ.get("WORKBENCH_DIR", "").strip()
    if v:
        return v, "环境变量 WORKBENCH_DIR（bat 启动器）"
    v = _settings_workdir()
    if v:
        return v, "workbench_settings.json（界面内切换记录）"
    v = _PORTABLE_CFG.get("WORKDIR", "").strip()
    if v:
        return v, "workbench2.cfg（bat 配置向导）"
    return _WORK_DIR_DEFAULT, "代码默认值"


WORK_DIR, WORK_DIR_SOURCE = _resolve_work_dir()
SYSTEM_NPM, SYSTEM_NPM_SOURCE = _resolve_npm(
    _override("WORKBENCH_SYSTEM_NPM", "NODE_NPM", _SYSTEM_NPM_DEFAULT))
FNM_NPM, FNM_NPM_SOURCE = _resolve_fnm_npm(
    _override("WORKBENCH_FNM_NPM", "FNM_NPM", _FNM_NPM_DEFAULT), SYSTEM_NPM)

# ---------------- 日志配置 ----------------
LOG_LEVELS = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40}
_LEVEL_TO_TAG = {"DEBUG": "debug", "INFO": "info", "WARN": "warn", "ERROR": "err"}
_TAG_TO_LEVEL = {"info": "INFO", "ok": "INFO", "err": "ERROR", "warn": "WARN", "debug": "DEBUG"}
LOG_DIR = os.path.join(APP_ROOT, "logs")
LOG_FILE = os.path.join(LOG_DIR, time.strftime("workbench-%Y%m%d.log"))
INITIAL_LOG_LEVEL = os.environ.get("WORKBENCH_LOG_LEVEL", "INFO").upper()
if INITIAL_LOG_LEVEL not in LOG_LEVELS:
    INITIAL_LOG_LEVEL = "INFO"
UPGRADE_TIMEOUT = 600  # 升级子进程超时上限（秒）


def dur(t0):
    """自 t0 起的耗时描述，用于日志字段。"""
    return f"{time.time() - t0:.1f}s"


def _npm_line_level(line):
    """根据 npm 输出特征推断日志级别，便于分级展示与检索。"""
    l = line.lower()
    if l.startswith("npm error") or "npm err!" in l or l.startswith("error"):
        return "ERROR"
    if l.startswith("npm warn") or l.startswith("warn") or "deprecat" in l:
        return "WARN"
    return "INFO"


def _kill_tree(pid):
    """强制结束整个进程树。

    npm 会派生 node.exe 子进程，只杀主进程会留下僵尸子进程
    （历史案例：僵尸 node.exe 持续占用包目录文件句柄，导致后续重装失败）。
    """
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, creationflags=NO_WINDOW, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        pass


# ---------------- 服务定义 ----------------
# min_node：该包 engines.node 的最低 Node 版本（取自 npm registry，参考值）。
# 用于提前兼容性告警（低位 Node 环境升级大概率 EBADENGINE 失败），不阻断操作。
SERVICES = [
    {"key": "claude", "name": "Claude", "cmd": "claude --dangerously-skip-permissions",
     "desc": "claude --dangerously-skip-permissions",
     "latest_cmd": [SYSTEM_NPM, "view", "@anthropic-ai/claude-code", "version"],
     "upgrade_cmd": [SYSTEM_NPM, "install", "-g", "@anthropic-ai/claude-code@latest"],
     "min_node": "22",           # engines: node >=22.0.0
     "proxy_default": False},
    {"key": "hermes", "name": "Hermes", "cmd": "hermes",
     "desc": "通过环境变量直接执行 hermes 命令",
     "latest_cmd": None,          # git 安装，无 npm 最新版可查
     "update_check": True,        # 用 `hermes update --check` 判断是否有更新
     "upgrade_cmd": None,         # 运行时解析为 hermes update
     "proxy_default": True},      # GitHub 需代理才能连通，默认开启
    {"key": "codex", "name": "Codex", "cmd": "codex -s danger-full-access -a never",
     "desc": "codex -s danger-full-access -a never",
     "latest_cmd": [SYSTEM_NPM, "view", "@openai/codex", "version"],
     "upgrade_cmd": [SYSTEM_NPM, "install", "-g", "@openai/codex@latest"],
     "min_node": "16",           # engines: node >=16
     "proxy_default": False},
    {"key": "pi", "name": "Pi", "cmd": "pi",
     "desc": "通过环境变量直接执行 pi 命令",
     "latest_cmd": [FNM_NPM, "view", "@earendil-works/pi-coding-agent", "version"],
     "upgrade_cmd": [FNM_NPM, "install", "-g", "@earendil-works/pi-coding-agent@latest"],
     "min_node": "22.19",        # engines: node >=22.19.0
     "proxy_default": False},
]

# ---------------- 用户设置（代理开关 / hermes 更新分支 / 工作目录，持久化） ----------------
HERMES_BRANCH_DEFAULT = "main"  # 上游无 stable 分支，main 即官方稳定发布线


class WorkbenchSettings:
    """用户可变设置：各服务「系统代理」开关 + hermes 更新分支。

    持久化到脚本同目录 workbench_settings.json：
    - 每次修改立即落盘，工作台重启后保持生效；
    - 文件缺失/损坏时回退默认值（hermes 代理开、其余关、分支 main），
      并在下一次修改时重建文件。
    """

    def __init__(self, path=SETTINGS_PATH):
        self.path = path
        self.data = {}
        self._load()

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        # 代理开关：文件里缺失的服务回退到 SERVICES 中的默认值
        pe = data.get("proxy_enabled")
        if not isinstance(pe, dict):
            pe = {}
        for s in SERVICES:
            pe.setdefault(s["key"], bool(s.get("proxy_default", False)))
        data["proxy_enabled"] = pe
        # hermes 更新分支：空值/缺失回退稳定版 main
        branch = str(data.get("hermes_branch") or "").strip()
        data["hermes_branch"] = branch or HERMES_BRANCH_DEFAULT
        self.data = data

    def save(self):
        """立即落盘（失败静默，不影响主流程）。"""
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    # ---- 代理开关 ----
    def proxy_enabled(self, key):
        return bool(self.data.get("proxy_enabled", {}).get(key, False))

    def set_proxy(self, key, on):
        self.data.setdefault("proxy_enabled", {})[key] = bool(on)

    # ---- hermes 更新分支 ----
    @property
    def hermes_branch(self):
        return str(self.data.get("hermes_branch") or HERMES_BRANCH_DEFAULT)

    def set_hermes_branch(self, branch):
        b = str(branch or "").strip()
        if b:
            self.data["hermes_branch"] = b
        return self.hermes_branch

    # ---- 工作目录（界面内「切换目录」写入，与 workbench2.cfg 双写保持一致） ----
    @property
    def workdir(self):
        return str(self.data.get("workdir") or "").strip()

    def set_workdir(self, d):
        d = str(d or "").strip()
        if d:
            self.data["workdir"] = os.path.normpath(d)
        return self.workdir


SETTINGS = WorkbenchSettings()

# 工作目录链路自愈：本次以 bat 传入的 env 为准，且与界面内切换记录不一致时，
# 把 settings 同步为当前值——直启 service_manager.py（不经 bat）时也用最新目录。
_env_dir = os.environ.get("WORKBENCH_DIR", "").strip()
if _env_dir and os.path.normpath(_env_dir) != os.path.normpath(SETTINGS.workdir or ""):
    SETTINGS.set_workdir(_env_dir)
    SETTINGS.save()

STATUS_STOPPED = "stopped"
STATUS_RUNNING = "running"
STATUS_MISSING = "missing"   # 命令未找到
STATUS_CRASHED = "crashed"   # 意外崩溃（计数器自增后由看门狗自动重启）

COLORS = {
    STATUS_RUNNING: ("#16a34a", "运行中"),
    STATUS_STOPPED: ("#6b7280", "已停止"),
    STATUS_MISSING: ("#dc2626", "未找到命令"),
    STATUS_CRASHED: ("#dc2626", "已崩溃（看门狗将自动重启）"),
}


# ---------------- 命令查找 ----------------
def _fnm_install_dirs():
    """fnm 管理的 Node 安装目录（默认别名优先，其次所有版本，新版本在前）。

    fnm 只在 PowerShell 配置文件里通过 `fnm env` 动态注入 PATH，
    其他进程（本工作台、cmd、Git Bash）看不到这些目录，
    因此这里显式搜索 fnm 的安装目录（FNM_DIR > %APPDATA%\fnm > %LOCALAPPDATA%\fnm）
    作为命令查找的兜底。
    """
    dirs = []
    for base in _fnm_bases():
        default_dir = os.path.join(base, "aliases", "default")
        if os.path.isdir(default_dir):  # junction，直接指向某个版本 installation
            dirs.append(default_dir)
        for d in sorted(glob.glob(os.path.join(base, "node-versions", "*", "installation")),
                        reverse=True):
            if os.path.isdir(d) and d not in dirs:
                dirs.append(d)
    return dirs


def which_anywhere(name):
    """先查 PATH，找不到再搜 fnm 目录；返回可执行文件完整路径或 None。"""
    hit = shutil.which(name)
    if hit:
        return hit
    exts = [e for e in os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(";") if e]
    # 优先带 Windows 扩展名的（.cmd/.exe 可直接运行），最后才是无扩展的 bash shim
    candidates = [name + e for e in exts] + [name + e.lower() for e in exts] + [name]
    for d in _fnm_install_dirs():
        for n in candidates:
            p = os.path.join(d, n)
            if os.path.isfile(p):
                return p
    return None


def to_exec(argv):
    """批处理（.cmd/.bat）需要经 cmd.exe 启动。"""
    if argv and argv[0].lower().endswith((".cmd", ".bat")):
        return ["cmd.exe", "/c"] + argv
    return list(argv)


def npm_env():
    """npm 操作专用环境：剥离 NODE_OPTIONS 注入（如 safe-delete 拦截器）。

    某些开发环境会通过 NODE_OPTIONS 向 Node/npm 注入安全删除拦截器，
    该拦截器在 junction 映射的 npm 全局目录上执行 trash 会失败，
    导致 npm install -g 升级中途崩溃并留下损坏的安装（启动脚本丢失）。
    升级/查询版本时必须使用干净环境。
    """
    env = os.environ.copy()
    env.pop("NODE_OPTIONS", None)
    return env


# ---------------- 网络代理（hermes 检查/升级专用） ----------------
# 结果缓存 60s：避免重试期间反复读注册表/探测端口
_proxy_cache = {"ts": 0.0, "value": None, "source": None}
PROXY_TTL = 60


def _port_open(host, port, timeout=0.3):
    """快速探测本机端口是否有服务监听（TCP 连接即认为代理在跑）。"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _probe_local_proxy():
    """兜底：探测本机常见代理客户端端口（Clash/Clash Verge/v2rayN/Privoxy 等）。

    仅在注册表未开启系统代理时使用。端口按「HTTP 代理」优先探测，
    全部不通时再试 socks 端口（git 支持 socks5:// 形式的代理环境变量）。
    """
    for port in (7890, 7897, 10809, 8118, 8888):  # 常见 HTTP/混合端口
        if _port_open("127.0.0.1", port):
            return f"http://127.0.0.1:{port}"
    for port in (1080, 10808):  # 常见 SOCKS 端口
        if _port_open("127.0.0.1", port):
            return f"socks5://127.0.0.1:{port}"
    return None


def get_proxy(force=False):
    """获取代理地址与来源；返回 (proxy_url 或 None, source 描述)。"""
    now = time.time()
    if not force and _proxy_cache["ts"] and now - _proxy_cache["ts"] < PROXY_TTL:
        return _proxy_cache["value"], _proxy_cache["source"]
    proxy, source = None, None
    explicit = os.environ.get("WORKBENCH_PROXY", "").strip()
    if explicit:
        proxy = explicit if "://" in explicit else "http://" + explicit
        source = "环境变量 WORKBENCH_PROXY"
    if proxy is None:
        for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
            if os.environ.get(k, "").strip():
                proxy, source = os.environ[k].strip(), f"继承环境变量 {k}"
                break
    if proxy is None:
        p = _registry_proxy()
        if p:
            proxy, source = p, "Windows 系统代理（注册表）"
    if proxy is None:
        p = _probe_local_proxy()
        if p:
            proxy, source = p, "本机代理端口探测"
    _proxy_cache.update(ts=now, value=proxy, source=source)
    return proxy, source


# 子进程环境里需要统一读写的代理变量（大小写都设置/清除，兼容 git/npm/各 CLI 实现）
PROXY_ENV_KEYS = ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy",
                  "ALL_PROXY", "all_proxy")
# 开关关闭时额外清除的代理相关变量（NO_PROXY 仅在无代理时有意义，一并清掉保持环境干净）
PROXY_CLEAR_KEYS = PROXY_ENV_KEYS + ("NO_PROXY", "no_proxy")


def service_net_env(key, logger=None, mod=None):
    """按服务的「系统代理」开关构造子进程环境。

    作用范围：服务启动进程、「检查更新」与「升级」子进程。
    - 开关开启：注入自动检测到的代理地址（git/npm/多数 CLI 遵循这些变量），
      保留继承的 NO_PROXY（localhost 等地址继续直连，语义正确）；
    - 开关关闭：显式清除全部代理相关变量——即使工作台本身是从带代理变量的
      会话启动的，也保证该服务相关子进程直连，行为与界面显示一致；
    - 始终基于 npm_env()（剥离 NODE_OPTIONS 注入，见其文档说明）。

    生效时机：本函数在每次子进程启动前调用，读取的是当前开关状态，
    因此检查更新/升级立即生效；服务进程则需重启后才按新开关注入。
    """
    env = npm_env()
    if SETTINGS.proxy_enabled(key):
        proxy, source = get_proxy()
        if proxy:
            for k in PROXY_ENV_KEYS:
                env[k] = proxy
            if logger:
                logger.debug(mod or key, f"{key}：系统代理已启用，注入代理变量",
                             proxy=proxy, source=source)
        else:
            if logger:
                logger.warn(mod or key,
                            f"{key}：系统代理开关已开启，但未检测到可用代理，本次将直连",
                            suggestion="开启系统/浏览器代理后重试，或设置环境变量 "
                                       "WORKBENCH_PROXY=127.0.0.1:端口 后重启工作台")
    else:
        for k in PROXY_CLEAR_KEYS:
            env.pop(k, None)
        if logger:
            logger.debug(mod or key, f"{key}：系统代理已禁用，已清除代理变量（直连）")
    return env


def resolve_command(cmd_line):
    """把命令行解析成 Popen 参数列表；返回 (argv, exe_path) 或 (None, None)。"""
    parts = cmd_line.split()
    exe = which_anywhere(parts[0])
    if exe is None:
        return None, None
    args = parts[1:]
    if exe.lower().endswith((".cmd", ".bat")):
        argv = ["cmd.exe", "/c", exe] + args
    else:
        argv = [exe] + args
    return argv, exe


def ver_tuple(v):
    """把版本字符串转成可比较的元组。"""
    try:
        return tuple(int(x) for x in v.split("."))
    except (ValueError, AttributeError):
        return (0,)


# ---------------- Node.js 环境探测（兼容低位版本 / 32 位安装） ----------------
_ARCH_LABELS = {"x64": "64 位（x64）", "ia32": "32 位（x86）", "arm64": "ARM64"}
_NODE_ENV_CACHE = {"value": None}


def _detect_node_env():
    """探测 node.exe 的版本与体系结构；返回 (version, arch, exe_path)。

    查找顺序：与系统 npm 同目录的 node.exe → 与 fnm npm 同目录 → PATH。
    兼容性说明：`node -p` 自 Node 0.10 起即可用，低位版本也能探测；
    探测失败（未装 Node / 执行异常）返回 (None, None, None)，不抛异常。
    """
    node_exe = None
    for npm_path in (SYSTEM_NPM, FNM_NPM):
        if npm_path and os.path.isfile(npm_path):
            cand = os.path.join(os.path.dirname(npm_path), "node.exe")
            if os.path.isfile(cand):
                node_exe = cand
                break
    if node_exe is None:
        node_exe = shutil.which("node.exe") or shutil.which("node")
    if node_exe is None:
        return None, None, None
    try:
        r = subprocess.run([node_exe, "-p", "process.version + ' ' + process.arch"],
                           capture_output=True, text=True, timeout=30,
                           creationflags=NO_WINDOW, encoding="utf-8",
                           errors="replace", env=npm_env())
    except (OSError, subprocess.TimeoutExpired):
        return None, None, node_exe
    out = (r.stdout or "").strip()
    m = re.search(r"v?(\d+(?:\.\d+)+)\s+([A-Za-z0-9_]+)", out)
    if m:
        return m.group(1), m.group(2).lower(), node_exe
    m = VERSION_RE.search(out)
    if m:
        return m.group(1), None, node_exe
    return None, None, node_exe


def node_env(force=False):
    """获取 Node 版本/架构/可执行路径（结果缓存；force=True 强制重探）。"""
    if force or _NODE_ENV_CACHE["value"] is None:
        _NODE_ENV_CACHE["value"] = _detect_node_env()
    return _NODE_ENV_CACHE["value"]


def arch_label(arch):
    """架构显示文本：x64/ia32/arm64 → 中文可读标签。"""
    return _ARCH_LABELS.get(arch or "", arch or "")


def node_compat_note(spec):
    """检查当前 Node 环境是否满足服务的最低要求；返回警告文本或 None。

    min_node 为该包 engines.node 的最低版本（npm registry 的参考值），仅用于
    提前告警，不阻断操作——是否强制取决于 npm engine-strict 配置与工具自身检查。
    """
    ver, arch, _exe = node_env()
    notes = []
    min_node = str(spec.get("min_node") or "").strip()
    if ver and min_node and ver_tuple(ver) < ver_tuple(min_node):
        notes.append(f"Node v{ver} 低于 {spec['name']} 要求的 v{min_node}+")
    if arch == "ia32":
        notes.append("当前 Node 为 32 位（x86），多数新版 CLI 仅提供 64 位支持")
    return "；".join(notes) if notes else None


# ---------------- 工作目录切换（界面内直接切换，持久化） ----------------
def set_work_dir(new_dir, persist=True):
    """切换工作目录（模块级全局，立即影响后续启动的服务与升级命令）。

    persist=True 时同时写回 workbench_settings.json 与 workbench2.cfg，
    确保经 bat 启动器或直接运行 service_manager.py 的下次启动都使用新目录。
    返回是否成功持久化（cfg 写失败即 False，由调用方提示用户）。
    """
    global WORK_DIR
    WORK_DIR = os.path.normpath(new_dir)
    ok = True
    if persist:
        SETTINGS.set_workdir(WORK_DIR)
        SETTINGS.save()
        ok = _save_cfg_workdir(WORK_DIR)
    return ok


def _save_cfg_workdir(new_dir):
    """把工作目录写回 workbench2.cfg（KEY=VALUE、CRLF、无 BOM、系统码页优先）。

    bat 以系统码页（中文 Windows=GBK）读取该文件，因此优先用 mbcs 写入保证
    双向兼容；路径含码页外字符时回退 UTF-8（此时仅 Python 直启可正确读取）。
    其余配置项（PY_EXE/NODE_NPM 等）原样保留。
    """
    text = _read_text_dual(_CFG_PATH)
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    out, replaced = [], False
    for ln in lines:
        if ln.split("=", 1)[0].strip().upper() == "WORKDIR":
            out.append(f"WORKDIR={new_dir}")
            replaced = True
        else:
            out.append(ln)
    if not replaced:
        out.append(f"WORKDIR={new_dir}")
    data = None
    for enc in ("mbcs", "utf-8"):
        try:
            data = ("\r\n".join(out) + "\r\n").encode(enc)
            break
        except (UnicodeEncodeError, LookupError):
            continue
    if data is None:
        return False
    try:
        with open(_CFG_PATH, "wb") as f:
            f.write(data)
        return True
    except OSError:
        return False


# ---------------- 结构化日志器 ----------------
class WorkbenchLogger:
    """结构化双通道日志器：界面日志区 + 持久化文件。

    - 格式：YYYY-MM-DD HH:MM:SS [级别] [模块] 描述 | key=value …
    - 文件始终记录全部级别（含 DEBUG 细节），排障时有完整现场；
    - 界面按当前级别过滤，可经 set_level 动态调整；
    - 线程安全：工作线程经 ui_queue 把日志投递到界面；文件写入加锁。
    """

    def __init__(self, ui_queue):
        self.ui_queue = ui_queue
        self.level_name = INITIAL_LOG_LEVEL
        self.level = LOG_LEVELS[INITIAL_LOG_LEVEL]
        self._file_lock = threading.Lock()
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
        except OSError:
            pass

    def set_level(self, name):
        name = name.upper()
        if name in LOG_LEVELS:
            self.level_name = name
            self.level = LOG_LEVELS[name]

    def write_file(self, line):
        """追加一行到日志文件（失败不影响主流程）。"""
        try:
            with self._file_lock:
                with open(LOG_FILE, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
        except OSError:
            pass

    def _emit(self, level, module, msg, fields, tag=None):
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        line = f"{ts} [{level}] [{module}] {msg}"
        if fields:
            parts = [f"{k}={v}" for k, v in fields.items() if v is not None]
            if parts:
                line += " | " + " ".join(parts)
        self.write_file(line)
        if LOG_LEVELS[level] >= self.level:
            self.ui_queue.put(("log", line, tag or _LEVEL_TO_TAG[level], False))

    def debug(self, module, msg, **fields):
        self._emit("DEBUG", module, msg, fields)

    def info(self, module, msg, **fields):
        self._emit("INFO", module, msg, fields)

    def success(self, module, msg, **fields):
        """成功结果：文件按 INFO 记录，界面显示绿色。"""
        self._emit("INFO", module, msg, fields, tag="ok")

    def warn(self, module, msg, **fields):
        self._emit("WARN", module, msg, fields)

    def error(self, module, msg, **fields):
        self._emit("ERROR", module, msg, fields)

    def log(self, level, module, msg, **fields):
        self._emit(level.upper(), module, msg, fields)


class ServiceState:
    def __init__(self, spec):
        self.spec = spec
        self.proc = None
        self.pid = None
        self.start_time = None
        self.status = STATUS_STOPPED
        # 版本相关
        self.version = None      # 当前版本
        self.latest = None       # 最新版本（npm 服务）
        self.update_status = None   # hermes 专用更新状态："current"/"available"/"unknown"
        self.update_detail = None   # hermes 更新状态补充说明（如落后提交数）
        self.ver_checked = False
        self.ver_checking = False
        self.upgrading = False
        # 升级回滚相关
        self.pre_upgrade_version = None   # 升级前版本快照
        self.pre_upgrade_commit = None    # hermes 升级前 git HEAD
        self.rollback_available = False   # 升级失败后是否可回滚
        # 崩溃自愈（看门狗）
        self.crash_count = 0          # 连续崩溃次数
        self.last_crash_time = 0.0    # 上次崩溃时间戳
        self.last_exit_code = None    # 最近一次退出码
        self.watchdog_enabled = True  # 默认开启看门狗
        # 资源监控
        self.cpu_percent = 0.0
        self.memory_mb = 0.0
        self.rss_bytes = 0
        self.resource_history = []    # 保留最近 30s 采样

    @property
    def running(self):
        return self.proc is not None and self.proc.poll() is None

    @property
    def has_update(self):
        if self.spec.get("update_check"):
            # hermes：由 `hermes update --check` 的结果直接判定
            return self.update_status == "available"
        return (self.latest is not None and self.version is not None
                and ver_tuple(self.latest) > ver_tuple(self.version))

    def uptime_text(self):
        if self.start_time is None or not self.running:
            return "-"
        secs = int(time.time() - self.start_time)
        h, rem = divmod(secs, 3600)
        m, s = divmod(rem, 60)
        if h:
            return f"{h}小时{m}分{s}秒"
        if m:
            return f"{m}分{s}秒"
        return f"{s}秒"

    def resource_text(self):
        if not self.running:
            return ""
        cpu = self.cpu_percent
        mem = self.memory_mb
        return f"CPU：{cpu:.1f}%  内存：{mem:.1f}MB"

    @property
    def should_auto_restart(self):
        """判断看门狗是否应自动重启此服务。"""
        return (self.watchdog_enabled
                and not self.running
                and self.crash_count > 0
                and self.crash_count <= 5
                and self.pid is not None)
