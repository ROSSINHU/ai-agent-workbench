# -*- coding: utf-8 -*-
r"""
AI agent管理工作台 —— 统一管理 claude / hermes / codex / pi 四个本地服务
功能：启动 / 停止 / 重启 / 实时状态（PID、运行时长）/ 版本检测 / 一键升级
工作目录、系统 npm、fnm npm 路径均可通过 工作台2.0.bat 探测配置并持久化到
workbench2.cfg，拷贝到其他 Windows 机器后只需重跑 bat 重新探测即可使用。

升级方式说明：
- claude  → 系统 npm（前缀 %APPDATA%\npm）install -g @anthropic-ai/claude-code@latest
- codex   → 系统 npm install -g @openai/codex@latest
- pi      → fnm 的 npm（前缀 fnm\aliases\default）install -g @earendil-works/pi-coding-agent@latest
- hermes  → 自带的 hermes update 子命令（git 安装方式）

网络代理与更新分支（用户设置，持久化到 workbench_settings.json）：
- 每个服务卡片上有「系统代理」开关，自行决定该服务是否经系统代理运行：
    开启 → 服务启动进程与「检查更新」/「升级」子进程注入代理环境变量；
    关闭 → 显式清除代理变量（直连），即使工作台从带代理变量的会话启动也不受污染。
- 代理地址自动检测，优先级：WORKBENCH_PROXY 环境变量 > 已有 HTTP(S)_PROXY
  环境变量 > Windows 系统代理（注册表，即浏览器代理设置）> 本机常见代理端口探测。
- 生效时机：检查更新/升级立即生效（每次执行时读取当前开关）；
  服务进程的环境变量在启动时注入，运行中的服务需「重启」后新设置才生效。
- hermes 更新分支默认 main：上游 NousResearch/hermes-agent 没有独立的 stable
  分支/标签，main 即官方稳定发布线；`hermes update --branch main` 会在本地
  检出其他开发分支时自动切回稳定线（卡片「更新分支」按钮可改为其他分支）。

工作目录（可在界面直接切换，持久化）：
- 顶栏「切换目录」按钮选择新目录：立即生效于后续启动的服务与升级命令，
  窗口标题与顶栏显示同步更新；正在运行的服务保持启动时的目录，重启后切换；
- 解析优先级：WORKBENCH_DIR 环境变量（bat 启动器显式传入，最高）
  > workbench_settings.json（界面内切换记录）> workbench2.cfg（bat 配置向导）
  > 代码默认值；界面内切换时同时写回 settings 与 cfg，保证经 bat 或直启一致。

Node.js 环境兼容（低位版本 / 32 位安装 / 各类安装位置）：
- npm 路径自动探测：配置路径不存在时依次尝试标准安装目录（含 32 位
  Program Files (x86)、按用户安装的 LOCALAPPDATA\Programs\nodejs、nvm
  symlink）→ fnm 默认别名 → PATH 兜底，不再绑定单一硬编码路径；
- 启动时探测 node.exe 的版本与体系结构（x64/ia32/arm64）：低于服务包
  engines.node 要求（见 SERVICES 的 min_node，取自 npm registry）或为
  32 位环境时提前告警；升级失败时识别 EBADENGINE 并建议升级 Node.js；
- 服务命令解析（启动）不依赖 npm 路径：走 PATH + fnm 目录兜底，不受影响。

日志系统说明：
- 结构化格式：YYYY-MM-DD HH:MM:SS [级别] [模块] 描述 | key=value …（便于按关键字检索）
- 双通道：界面日志区（按级别过滤）+ logs\workbench-YYYYMMDD.log 文件（全量持久化，
  含 DEBUG 细节，排障时界面关掉也有完整现场）
- 级别：DEBUG（子进程原始输出、参数细节）/ INFO（阶段节点与结果）
       / WARN（可疑但可继续，如处理建议）/ ERROR（失败，含原因与建议）
- 配置：界面「日志级别」下拉框实时切换；环境变量 WORKBENCH_LOG_LEVEL 设初始级别
- 升级流程为六阶段结构化执行，每阶段记录输入参数、耗时与结果；
  失败时输出针对性处理建议（沉淀自真实历史案例）
"""
import os
import re
import json
import glob
import time
import queue
import shutil
import socket
import traceback
import threading
import subprocess
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from tkinter.scrolledtext import ScrolledText

# ---- 可选依赖：资源监控 / 系统托盘 ----
_PSUTIL_AVAIL = False
_PYSTRAY_AVAIL = False
try:
    import psutil
    _PSUTIL_AVAIL = True
except ImportError:
    psutil = None  # type: ignore
try:
    import pystray
    from PIL import Image, ImageDraw, ImageFont
    _PYSTRAY_AVAIL = True
except ImportError:
    pystray = None
    Image = None

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
SETTINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "workbench_settings.json")
_CFG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "workbench2.cfg")


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
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
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


def _registry_proxy():
    """读取 Windows 系统代理（即浏览器使用的代理设置，IE/WinINET 共享）。

    注册表 HKCU\\...\\Internet Settings：
      ProxyEnable=1 且 ProxyServer 形如 "127.0.0.1:7890" 或
      "http=...;https=...;ftp=..."（按协议分别指定时取 https/http 条目）。
    """
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
        try:
            enable, _ = winreg.QueryValueEx(key, "ProxyEnable")
            if not enable:
                return None
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
        finally:
            winreg.CloseKey(key)
    except OSError:
        return None
    server = (server or "").strip()
    if not server:
        return None
    if "=" in server:  # 按协议分别指定：http=host:port;https=host:port;...
        picked = None
        for part in server.split(";"):
            proto, _, addr = part.partition("=")
            if proto.strip().lower() == "https":
                picked = addr.strip()
                break
            if proto.strip().lower() == "http" and picked is None:
                picked = addr.strip()
        server = picked or ""
    if not server:
        return None
    if "://" not in server:
        server = "http://" + server
    return server


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

class WorkbenchApp:
    REFRESH_MS = 1000

    def __init__(self, root):
        self.root = root
        self.root.title(f"AI agent管理工作台 — {WORK_DIR}")
        self.root.geometry("1000x740")
        self.root.minsize(860, 620)

        self.services = {s["key"]: ServiceState(s) for s in SERVICES}
        self.ui_queue = queue.Queue()
        self.logger = WorkbenchLogger(self.ui_queue)
        self.proxy_vars = {}   # 各服务「系统代理」开关的 BooleanVar（与 SETTINGS 同步）
        self._net_dirty = True  # 网络状态标签需要刷新（启动时/开关切换后置位）
        self._tray_icon = None       # 系统托盘图标
        self._tray_running = False    # 托盘线程是否运行中
        self._watchdog_lock = threading.Lock()
        self._build_ui()
        self.root.after(200, self.refresh_all)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close_tray_aware)
        # 启动后台资源监控线程（psutil 可用时）
        if _PSUTIL_AVAIL:
            self._res_monitor_started = False
        # 启动时自动做一轮版本检测
        for key in self.services:
            self.check_version(key, quiet=True)

    # ---------------- UI ----------------
    def _build_ui(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass

        # 顶部标题栏
        header = tk.Frame(self.root, bg="#1f2937", height=56)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(header, text="AI agent管理工作台", font=("Microsoft YaHei UI", 15, "bold"),
                 fg="white", bg="#1f2937").pack(side="left", padx=16)
        # 工作目录显示：与实际生效的 WORK_DIR 同步（切换目录后实时更新）
        self.workdir_lbl = tk.Label(header, text=f"工作目录：{WORK_DIR}",
                                    font=("Microsoft YaHei UI", 10),
                                    fg="#9ca3af", bg="#1f2937")
        self.workdir_lbl.pack(side="left", padx=4)
        tk.Button(header, text="切换目录", relief="flat", cursor="hand2",
                  bg="#374151", fg="#d1d5db", activebackground="#4b5563",
                  activeforeground="white", font=("Microsoft YaHei UI", 9),
                  command=self._switch_work_dir).pack(side="left", padx=(6, 0))
        btns = tk.Frame(header, bg="#1f2937")
        btns.pack(side="right", padx=12)
        tk.Button(btns, text="全部启动", command=self.start_all, width=9,
                  bg="#16a34a", fg="white", relief="flat", cursor="hand2",
                  activebackground="#15803d", activeforeground="white").pack(side="left", padx=4)
        tk.Button(btns, text="全部停止", command=self.stop_all, width=9,
                  bg="#dc2626", fg="white", relief="flat", cursor="hand2",
                  activebackground="#b91c1c", activeforeground="white").pack(side="left", padx=4)
        tk.Button(btns, text="全部检查更新", command=self.check_all_versions, width=11,
                  bg="#4b5563", fg="white", relief="flat", cursor="hand2",
                  activebackground="#374151", activeforeground="white").pack(side="left", padx=4)
        # 系统托盘开关（最小化到托盘运行）
        if _PYSTRAY_AVAIL:
            tk.Button(btns, text="托盘运行", command=self.toggle_tray_mode, width=8,
                      bg="#0d9488", fg="white", relief="flat", cursor="hand2",
                      activebackground="#0f766e", activeforeground="white").pack(side="left", padx=4)
        else:
            tk.Button(btns, text="托盘运行", state="disabled", width=8,
                      bg="#4b5563", fg="#9ca3af", relief="flat", cursor="hand2").pack(side="left", padx=4)

        # 服务卡片区
        cards_wrap = tk.Frame(self.root, bg="#f3f4f6")
        cards_wrap.pack(fill="both", expand=True, padx=12, pady=(12, 6))
        self.cards = {}
        for i, st in enumerate(self.services.values()):
            self.cards[st.spec["key"]] = self._build_card(cards_wrap, st, i)

        # 日志区
        log_wrap = tk.LabelFrame(self.root, text="操作日志", font=("Microsoft YaHei UI", 10),
                                 bg="#f3f4f6", fg="#374151")
        log_wrap.pack(fill="both", padx=12, pady=(6, 12))

        # 日志工具条：级别切换 + 日志文件位置 + 打开日志目录
        log_bar = tk.Frame(log_wrap, bg="#f3f4f6")
        log_bar.pack(fill="x", padx=8, pady=(6, 0))
        tk.Label(log_bar, text="日志级别：", bg="#f3f4f6", fg="#374151",
                 font=("Microsoft YaHei UI", 9)).pack(side="left")
        self.level_var = tk.StringVar(value=self.logger.level_name)
        cb = ttk.Combobox(log_bar, textvariable=self.level_var, state="readonly",
                          values=["DEBUG", "INFO", "WARN", "ERROR"], width=6)
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", self._on_level_change)
        tk.Label(log_bar, text=f"日志文件：{LOG_FILE}（文件记录全量，界面按级别过滤）",
                 bg="#f3f4f6", fg="#9ca3af", font=("Microsoft YaHei UI", 8),
                 anchor="w").pack(side="left", padx=10)
        tk.Button(log_bar, text="打开日志目录", relief="flat", cursor="hand2",
                  bg="#e5e7eb", fg="#374151", activebackground="#d1d5db",
                  activeforeground="#111827", command=self._open_log_dir).pack(side="right")

        self.log = ScrolledText(log_wrap, height=9, font=("Consolas", 9),
                                bg="#111827", fg="#d1d5db", state="disabled",
                                wrap="word", relief="flat")
        self.log.pack(fill="both", expand=True, padx=8, pady=(2, 8))
        for tag, color in (("ok", "#4ade80"), ("err", "#f87171"), ("info", "#93c5fd"),
                           ("warn", "#fbbf24"), ("debug", "#6b7280")):
            self.log.tag_configure(tag, foreground=color)

    def _build_card(self, parent, st, index):
        card = tk.Frame(parent, bg="white", bd=1, relief="solid",
                        highlightbackground="#e5e7eb", highlightthickness=1)
        # 2x2 布局
        card.grid(row=index // 2, column=index % 2, sticky="nsew", padx=8, pady=8)
        parent.columnconfigure(0, weight=1)
        parent.columnconfigure(1, weight=1)
        parent.rowconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        inner = tk.Frame(card, bg="white")
        inner.pack(fill="both", expand=True, padx=14, pady=10)

        # 第一行：服务名 + 状态
        top = tk.Frame(inner, bg="white")
        top.pack(fill="x")
        tk.Label(top, text=st.spec["name"], font=("Microsoft YaHei UI", 13, "bold"),
                 bg="white", fg="#111827").pack(side="left")
        dot_lbl = tk.Label(top, text="●", font=("Microsoft YaHei UI", 12), bg="white")
        dot_lbl.pack(side="left", padx=(12, 3))
        status_lbl = tk.Label(top, text="已停止", font=("Microsoft YaHei UI", 11, "bold"), bg="white")
        status_lbl.pack(side="left")

        # 第二行：命令
        tk.Label(inner, text=st.spec["desc"], font=("Consolas", 9),
                 bg="white", fg="#6b7280", anchor="w").pack(fill="x", pady=(4, 0))

        # 第三行：PID / 运行时长
        info_lbl = tk.Label(inner, text="PID：-    运行时长：-", font=("Microsoft YaHei UI", 9),
                            bg="white", fg="#374151", anchor="w")
        info_lbl.pack(fill="x", pady=(2, 0))

        # 资源监控行（仅 psutil 可用时显示）
        res_lbl = tk.Label(inner, text="" if _PSUTIL_AVAIL else "（资源监控：需安装 psutil）",
                           font=("Microsoft YaHei UI", 9),
                           bg="white", fg="#9ca3af" if not _PSUTIL_AVAIL else "#374151", anchor="w")
        res_lbl.pack(fill="x", pady=(1, 0))

        # 第四行：网络设置状态（系统代理 开/关；hermes 另有「更新分支」入口）
        net = tk.Frame(inner, bg="white")
        net.pack(fill="x", pady=(2, 0))
        proxy_lbl = tk.Label(net, text="代理：-", font=("Microsoft YaHei UI", 9),
                             bg="white", fg="#6b7280", anchor="w")
        proxy_lbl.pack(side="left")
        branch_btn = None
        if st.spec.get("update_check"):  # 仅 hermes：更新分支设置入口
            branch_btn = tk.Button(
                net, text=f"更新分支:{SETTINGS.hermes_branch}", relief="flat", cursor="hand2",
                bg="white", fg="#2563eb", activebackground="#f3f4f6",
                activeforeground="#1d4ed8", font=("Microsoft YaHei UI", 9),
                command=lambda k=st.spec["key"]: self.configure_branch(k))
            branch_btn.pack(side="right")

        # 第五行：版本信息
        ver_lbl = tk.Label(inner, text="版本：-", font=("Microsoft YaHei UI", 9),
                           bg="white", fg="#6b7280", anchor="w")
        ver_lbl.pack(fill="x", pady=(2, 8))

        # 按钮行
        btns = tk.Frame(inner, bg="white")
        btns.pack(fill="x")
        b_start = tk.Button(btns, text="启动", width=6, relief="flat", cursor="hand2",
                            bg="#16a34a", fg="white", activebackground="#15803d",
                            activeforeground="white",
                            command=lambda: self.start_service(st.spec["key"]))
        b_stop = tk.Button(btns, text="停止", width=6, relief="flat", cursor="hand2",
                           bg="#dc2626", fg="white", activebackground="#b91c1c",
                           activeforeground="white",
                           command=lambda: self.stop_service(st.spec["key"]))
        b_restart = tk.Button(btns, text="重启", width=6, relief="flat", cursor="hand2",
                              bg="#2563eb", fg="white", activebackground="#1d4ed8",
                              activeforeground="white",
                              command=lambda: self.restart_service(st.spec["key"]))
        b_check = tk.Button(btns, text="检查更新", width=8, relief="flat", cursor="hand2",
                            bg="#e5e7eb", fg="#374151", activebackground="#d1d5db",
                            activeforeground="#111827",
                            command=lambda: self.check_version(st.spec["key"]))
        b_upgrade = tk.Button(btns, text="升级", width=6, relief="flat", cursor="hand2",
                              bg="#d97706", fg="white", activebackground="#b45309",
                              activeforeground="white",
                              command=lambda: self.upgrade_service(st.spec["key"]))
        b_rollback = tk.Button(btns, text="回滚", width=6, relief="flat", cursor="hand2",
                               bg="#7c3aed", fg="white", activebackground="#6d28d9",
                               activeforeground="white", state="disabled",
                               command=lambda: self.rollback_service(st.spec["key"]))
        b_start.pack(side="left", padx=(0, 6))
        b_stop.pack(side="left", padx=(0, 6))
        b_restart.pack(side="left", padx=(0, 6))
        b_check.pack(side="left", padx=(0, 6))
        b_upgrade.pack(side="left")
        b_rollback.pack(side="left")
        # 「系统代理」开关（持久化）：控制该服务是否经系统代理运行
        # 「系统代理」开关（持久化）：控制该服务是否经系统代理运行
        pv = tk.BooleanVar(value=SETTINGS.proxy_enabled(st.spec["key"]))
        self.proxy_vars[st.spec["key"]] = pv
        proxy_chk = tk.Checkbutton(
            btns, text="系统代理", variable=pv, bg="white", fg="#374151",
            font=("Microsoft YaHei UI", 9), activebackground="white", cursor="hand2",
            command=lambda k=st.spec["key"]: self._on_proxy_toggle(k))
        proxy_chk.pack(side="right")

        return {"dot": dot_lbl, "status": status_lbl, "info": info_lbl, "ver": ver_lbl,
                "net": proxy_lbl, "branch": branch_btn,
                "start": b_start, "stop": b_stop, "restart": b_restart,
                "check": b_check, "upgrade": b_upgrade, "rollback": b_rollback,
                "res": res_lbl}

    # ---------------- 日志 ----------------
    def log_msg(self, text, tag="info", to_file=True):
        """界面直写日志（主线程用）；默认同步落盘，级别由 tag 推断。

        工作线程请使用 self.logger（经 ui_queue 投递，自带落盘）。
        """
        ts = time.strftime("%H:%M:%S")
        self.log.configure(state="normal")
        self.log.insert("end", f"[{ts}] {text}\n", tag)
        self.log.see("end")
        self.log.configure(state="disabled")
        if to_file:
            level = _TAG_TO_LEVEL.get(tag, "INFO")
            self.logger.write_file(
                f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{level}] [ui] {text}")

    def _on_level_change(self, _evt=None):
        self.logger.set_level(self.level_var.get())
        self.log_msg(f"日志级别已切换为 {self.logger.level_name}"
                     f"（文件日志始终记录全部级别）", "info")

    def _open_log_dir(self):
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            os.startfile(LOG_DIR)
        except OSError as e:
            self.log_msg(f"打开日志目录失败：{e}", "err")

    # ---------------- 工作目录切换（界面内直接切换，持久化） ----------------
    def _refresh_workdir_display(self):
        """窗口标题与顶栏显示同步为当前工作目录。"""
        self.root.title(f"AI agent管理工作台 — {WORK_DIR}")
        self.workdir_lbl.configure(text=f"工作目录：{WORK_DIR}")

    def _switch_work_dir(self):
        """选择新工作目录：立即生效于后续操作，显示同步更新，配置双写持久化。

        生效时机（与「系统代理」开关一致的原则）：
        - 检查更新 / 升级 / 新启动的服务 → 立即使用新目录；
        - 正在运行的服务进程 → 工作目录在进程启动时已固定，需「重启」后切换；
        - 持久化：workbench_settings.json + workbench2.cfg 双写，
          重启工作台 / 经 工作台2.0.bat 启动均使用新目录。
        """
        # 升级进行中禁止切换：升级子进程的 cwd 在启动时已固定，
        # 且升级后比对依赖启动时的目录上下文，中途切换会产生混淆
        upgrading = [st.spec["name"] for st in self.services.values() if st.upgrading]
        if upgrading:
            messagebox.showwarning("升级进行中",
                                   "以下服务正在升级，请等待完成后再切换工作目录：\n  "
                                   + "\n  ".join(upgrading))
            return
        initial = WORK_DIR if os.path.isdir(WORK_DIR) else os.path.expanduser("~")
        new = filedialog.askdirectory(title="选择新的工作目录", initialdir=initial,
                                      parent=self.root)
        if not new:
            return
        new = os.path.normpath(new)
        if new.lower() == os.path.normpath(WORK_DIR).lower():
            self.log_msg(f"工作目录未变化：{WORK_DIR}", "info")
            return
        running = [st.spec["name"] for st in self.services.values() if st.running]
        if running:
            if not messagebox.askyesno(
                    "切换工作目录",
                    f"将工作目录切换为：\n  {new}\n\n"
                    f"以下服务正在运行，仍保持启动时的目录（重启后切换）：\n  "
                    + "、".join(running) + "\n\n确认切换？"):
                return
        old = WORK_DIR
        saved = set_work_dir(new, persist=True)
        self._refresh_workdir_display()
        self.log_msg(f"工作目录已切换：{old} → {new}", "ok")
        if saved:
            self.log_msg("已保存配置（重启工作台后仍生效；工作台2.0.bat 下次启动将使用新目录）",
                         "info")
        else:
            self.log_msg("配置文件写入失败：重启后可能恢复旧目录，请检查磁盘权限", "warn")
        if running:
            self.log_msg(f"正在运行的服务（{'、'.join(running)}）请点击「重启」"
                         f"使其切换到新目录", "warn")

    # ---------------- 代理开关 / 更新分支（用户设置，持久化） ----------------
    def _on_proxy_toggle(self, key):
        """「系统代理」开关切换：立即保存，并明确提示生效范围。"""
        st = self.services[key]
        name = st.spec["name"]
        mod = f"{key}.proxy"
        on = self.proxy_vars[key].get()
        SETTINGS.set_proxy(key, on)
        SETTINGS.save()  # 持久化：工作台重启后保持
        if on:
            proxy, source = get_proxy()
            if proxy:
                self.logger.success(mod, f"{name} 系统代理已启用（已保存，重启工作台后仍生效）",
                                    proxy=proxy, source=source)
            else:
                self.logger.warn(mod, f"{name} 系统代理已启用，但当前未检测到可用代理",
                                 suggestion="开启系统/浏览器代理后重新「检查更新」；"
                                            "或设置环境变量 WORKBENCH_PROXY=127.0.0.1:端口 "
                                            "后重启工作台")
        else:
            self.logger.info(mod, f"{name} 系统代理已禁用（直连，已保存）")
        # 生效时机（需求明确）：
        #   检查更新 / 升级 → 立即生效（每次执行时读取当前开关）
        #   服务进程       → 环境变量在启动时注入，需重启后生效
        if st.running:
            self.logger.warn(mod, f"{name} 正在运行（PID {st.pid}）："
                                  f"代理设置将在下次「重启」后对该进程生效",
                             suggestion="点击「重启」让新代理设置立即作用于服务进程")
        else:
            self.logger.info(mod, "生效范围：检查更新/升级立即生效；"
                                  "服务下次启动时按此设置注入环境变量")
        self._net_dirty = True

    def configure_branch(self, key):
        """弹出 hermes 更新分支设置对话框（保存后立即生效）。"""
        if not self.services[key].spec.get("update_check"):
            return
        win = tk.Toplevel(self.root)
        win.title("Hermes 更新分支")
        win.geometry("480x240")
        win.resizable(False, False)
        win.transient(self.root)
        win.grab_set()
        frm = tk.Frame(win, bg="white", padx=16, pady=12)
        frm.pack(fill="both", expand=True)
        tk.Label(frm, text="更新分支（hermes update --branch）",
                 font=("Microsoft YaHei UI", 11, "bold"),
                 bg="white", fg="#111827").pack(anchor="w")
        tk.Label(frm, justify="left", font=("Microsoft YaHei UI", 9),
                 bg="white", fg="#6b7280",
                 text="上游 NousResearch/hermes-agent 没有独立的 stable 分支/标签，\n"
                      "main 即官方稳定发布线（所有正式版本均由 main 发布）。\n"
                      "若本地检出在其他开发分支，升级时会自动切回此处指定的分支。").pack(
            anchor="w", pady=(4, 10))
        row = tk.Frame(frm, bg="white")
        row.pack(fill="x")
        tk.Label(row, text="分支：", bg="white", fg="#374151",
                 font=("Microsoft YaHei UI", 10)).pack(side="left")
        var = tk.StringVar(value=SETTINGS.hermes_branch)
        cb = ttk.Combobox(row, textvariable=var, values=["main"], width=24)
        cb.pack(side="left")
        tip = tk.Label(frm, text="", bg="white", fg="#dc2626",
                       font=("Microsoft YaHei UI", 9))
        tip.pack(anchor="w", pady=(6, 0))

        def apply():
            b = var.get().strip()
            if not b or any(ch in b for ch in " \t"):
                tip.configure(text="分支名不能为空，且不能包含空格")
                return
            old = SETTINGS.hermes_branch
            SETTINGS.set_hermes_branch(b)
            SETTINGS.save()  # 持久化：工作台重启后仍生效
            self.logger.success("hermes.branch", "hermes 更新分支已设置（已保存，立即生效）",
                                branch=b, previous=old,
                                note="main=稳定发布线；下次「检查更新」/「升级」即按新分支执行")
            self._net_dirty = True
            win.destroy()

        btnrow = tk.Frame(frm, bg="white")
        btnrow.pack(fill="x", pady=(10, 0))
        tk.Button(btnrow, text="保存", width=8, relief="flat", cursor="hand2",
                  bg="#2563eb", fg="white", activebackground="#1d4ed8",
                  activeforeground="white", command=apply).pack(side="right", padx=(6, 0))
        tk.Button(btnrow, text="取消", width=8, relief="flat", cursor="hand2",
                  bg="#e5e7eb", fg="#374151", activebackground="#d1d5db",
                  activeforeground="#111827", command=win.destroy).pack(side="right")

    def _refresh_net_labels(self):
        """刷新各卡片的代理状态行与 hermes 分支按钮文字。

        仅在启动时/设置变化后调用（_net_dirty 控制），避免每次刷新都做
        代理探测（无代理的机器上端口探测可能耗时约 2s，会卡界面）。
        代理地址取自 get_proxy() 的 60s 缓存，开关状态永远实时。
        """
        proxy, _src = get_proxy()
        for key, st in self.services.items():
            c = self.cards[key]
            on = SETTINGS.proxy_enabled(key)
            if on and proxy:
                c["net"].configure(text=f"代理：已启用 {proxy}", fg="#16a34a")
            elif on:
                c["net"].configure(text="代理：已启用（未检测到代理，暂直连）", fg="#d97706")
            else:
                c["net"].configure(text="代理：已禁用（直连）", fg="#6b7280")
            if c.get("branch"):
                c["branch"].configure(text=f"更新分支:{SETTINGS.hermes_branch}")

    # ---------------- 核心操作 ----------------
    def start_service(self, key, quiet=False):
        st = self.services[key]
        name = st.spec["name"]
        if st.running:
            if not quiet:
                self.log_msg(f"{name} 已在运行中（PID {st.pid}）", "info")
            return True
        argv, exe = resolve_command(st.spec["cmd"])
        if argv is None:
            st.status = STATUS_MISSING
            self.refresh_all()
            self.log_msg(f"启动失败：未在 PATH 及 fnm 目录中找到命令 “{st.spec['cmd'].split()[0]}”，"
                         f"请先安装或将其加入环境变量", "err")
            return False
        # 代理开关开启 → 注入代理变量；关闭 → 清除代理变量（保证直连不被继承污染）。
        # 进程环境在启动时固定：切换开关后需重启服务，新设置才对该进程生效。
        try:
            proc = subprocess.Popen(
                argv,
                cwd=WORK_DIR,
                creationflags=subprocess.CREATE_NEW_CONSOLE,
                env=service_net_env(key, self.logger, f"{key}.start"),
            )
        except OSError as e:
            self.log_msg(f"启动 {name} 失败：{e}", "err")
            return False
        st.proc = proc
        st.pid = proc.pid
        st.start_time = time.time()
        st.status = STATUS_RUNNING
        self.refresh_all()
        net_state = "系统代理：开" if SETTINGS.proxy_enabled(key) else "系统代理：关（直连）"
        self.log_msg(f"{name} 已启动（PID {proc.pid}，工作目录 {WORK_DIR}，{net_state}）", "ok")
        return True

    def stop_service(self, key, quiet=False):
        st = self.services[key]
        name = st.spec["name"]
        if not st.running:
            if not quiet:
                self.log_msg(f"{name} 当前未运行", "info")
            return True
        pid = st.pid
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, creationflags=NO_WINDOW)
        except OSError as e:
            self.log_msg(f"停止 {name} 失败：{e}", "err")
            return False
        try:
            st.proc.wait(timeout=3)
        except Exception:
            pass
        st.proc = None
        st.pid = None
        st.start_time = None
        st.status = STATUS_STOPPED
        self.refresh_all()
        self.log_msg(f"{name} 已停止（PID {pid}）", "ok")
        return True

    def restart_service(self, key):
        name = self.services[key].spec["name"]
        self.log_msg(f"正在重启 {name} …", "info")
        self.stop_service(key, quiet=True)
        time.sleep(0.5)
        self.start_service(key, quiet=True)

    def start_all(self):
        self.log_msg("—— 全部启动 ——", "info")
        for key in self.services:
            self.start_service(key, quiet=True)

    def stop_all(self):
        self.log_msg("—— 全部停止 ——", "info")
        for key in self.services:
            self.stop_service(key, quiet=True)

    # ---------------- 版本检测与升级 ----------------
    def _fetch_current_version(self, st, mod=None):
        """获取当前版本；mod 非空时记录 DEBUG 级过程细节（命令、退出码、耗时、原始输出）。"""
        exe = which_anywhere(st.spec["cmd"].split()[0])
        if exe is None:
            if mod:
                self.logger.debug(mod, "版本检测：命令未找到，跳过",
                                  cmd=st.spec["cmd"].split()[0])
            return None
        t0 = time.time()
        try:
            r = subprocess.run(to_exec([exe, "--version"]), capture_output=True,
                               text=True, timeout=60, creationflags=NO_WINDOW,
                               encoding="utf-8", errors="replace", env=npm_env())
        except subprocess.TimeoutExpired:
            if mod:
                self.logger.warn(mod, "版本检测超时（60s），命令无响应", cmd=exe)
            return None
        except OSError as e:
            if mod:
                self.logger.warn(mod, "版本检测执行失败", cmd=exe, error=str(e))
            return None
        m = VERSION_RE.search(r.stdout or "") or VERSION_RE.search(r.stderr or "")
        ver = m.group(1) if m else None
        if ver is None and mod:
            # 命令存在但版本解析失败：若 Node 环境本身不满足要求，大概率是
            # 工具启动时自检失败（低位 Node / 32 位安装），提前给出定向提示
            note = node_compat_note(st.spec)
            if note:
                self.logger.warn(mod, "版本未能解析，且当前 Node 环境存在兼容性风险",
                                 risk=note,
                                 suggestion="该工具可能因 Node 版本过低/架构不符拒绝运行，"
                                            "升级 Node.js（64 位 LTS）后重新检测")
        if mod:
            self.logger.debug(mod, "版本检测完成", cmd=exe, exit_code=r.returncode,
                              parsed=ver or "未识别", elapsed=dur(t0),
                              output=(r.stdout or r.stderr or "").strip()[:120])
        return ver

    def _fetch_latest_version(self, st, mod=None):
        """查询 npm 最新版本；mod 非空时记录 DEBUG 级过程细节。"""
        cmd = st.spec["latest_cmd"]
        if not cmd or not os.path.isfile(cmd[0]):
            if mod:
                self.logger.warn(mod, "最新版本查询：npm 路径不存在，跳过",
                                  npm=cmd[0] if cmd else "未配置")
            return None
        t0 = time.time()
        try:
            # npm view 访问 registry，网络环境按该服务的代理开关决定
            r = subprocess.run(to_exec(cmd), capture_output=True, text=True,
                               timeout=90, creationflags=NO_WINDOW,
                               encoding="utf-8", errors="replace",
                               env=service_net_env(st.spec["key"], self.logger, mod))
        except subprocess.TimeoutExpired:
            if mod:
                self.logger.warn(mod, "最新版本查询超时（90s），网络可能不通", npm=cmd[0])
            return None
        except OSError as e:
            if mod:
                self.logger.warn(mod, "最新版本查询执行失败", npm=cmd[0], error=str(e))
            return None
        if r.returncode != 0:
            if mod:
                self.logger.warn(mod, "最新版本查询失败", npm=cmd[0], exit_code=r.returncode,
                                 error=(r.stderr or r.stdout or "").strip()[:120],
                                 suggestion="检查网络/代理，或 npm registry 配置")
            return None
        m = VERSION_RE.search(r.stdout or "")
        ver = m.group(1) if m else None
        if mod:
            self.logger.debug(mod, "最新版本查询完成", npm=cmd[0], latest=ver or "未识别",
                              elapsed=dur(t0))
        return ver

    # 网络类错误特征：命中即视为「瞬时故障，值得重试」
    NET_ERR_PATTERNS = (
        "network error", "unable to access", "could not resolve host",
        "timed out", "timeout", "connection reset", "connection refused",
        "connection aborted", "ssl", "gnu tls", "proxy", "empty reply",
        "failed to connect", "errno 10060", "errno 10061",
    )

    @classmethod
    def _is_network_error(cls, text):
        t = (text or "").lower()
        return any(p in t for p in cls.NET_ERR_PATTERNS)

    def _update_check_once(self, st, exe, mod=None):
        """执行一次 `hermes update --check --branch <分支>`。

        返回 (status, detail, retryable)：
          status = "current" | "available" | "unknown"
          retryable = True 表示瞬时网络故障，可以重试
        """
        t0 = time.time()
        branch = SETTINGS.hermes_branch  # 默认 main：上游无 stable 分支，main 即稳定发布线
        argv = to_exec([exe, "update", "--check", "--branch", branch])
        try:
            r = subprocess.run(argv, capture_output=True, text=True,
                               timeout=90, creationflags=NO_WINDOW,
                               encoding="utf-8", errors="replace",
                               env=service_net_env(st.spec["key"], self.logger, mod))
        except subprocess.TimeoutExpired:
            if mod:
                self.logger.warn(mod, "更新状态检查尝试超时（90s），git fetch 无响应",
                                 cmd=f"hermes update --check --branch {branch}",
                                 retryable=True, elapsed=dur(t0))
            return "unknown", "检查超时（90s）", True
        except OSError as e:
            # 进程级失败（无法启动等），重试大概率无益
            if mod:
                self.logger.warn(mod, "更新状态检查执行失败", cmd=exe, error=str(e),
                                 retryable=False)
            return "unknown", f"执行失败：{e}", False
        out = (r.stdout or "") + (r.stderr or "")
        if "Already up to date" in out:
            if mod:
                self.logger.debug(mod, "更新状态检查尝试成功", status="current",
                                  exit_code=r.returncode, elapsed=dur(t0))
            return "current", None, False
        m = re.search(r"Update available:?\s*(\d+)\s*commits?\s*behind\s*(\S+)", out)
        if m:
            detail = f"落后 {m.group(1)} 个提交（{m.group(2)}）"
            if mod:
                self.logger.debug(mod, "更新状态检查尝试成功", status="available",
                                  detail=detail, exit_code=r.returncode, elapsed=dur(t0))
            return "available", detail, False
        if "Update available" in out:
            if mod:
                self.logger.debug(mod, "更新状态检查尝试成功", status="available",
                                  exit_code=r.returncode, elapsed=dur(t0))
            return "available", "远端有新提交", False
        # 无法识别结果：区分网络瞬时故障与其他错误
        snippet = out.strip().replace("\n", " | ")[:150]
        net = self._is_network_error(out)
        if mod:
            self.logger.warn(mod, "更新状态检查尝试失败",
                             cmd=f"hermes update --check --branch {branch}",
                             exit_code=r.returncode,
                             error_kind="网络瞬时故障（将重试）" if net else "非网络错误（不重试）",
                             output=snippet or "(无输出)", elapsed=dur(t0))
        return "unknown", f"exit={r.returncode} {snippet}".strip(), net

    def _fetch_update_availability(self, st, mod=None,
                                   max_attempts=3, backoffs=(3, 8)):
        """hermes 专用：执行 `hermes update --check` 判断远端是否有更新（带重试）。

        该命令只做 git fetch + 比对，不修改工作区，安全无副作用。
        可靠性设计：
          - 网络类瞬时故障（GitHub 连接不稳）自动重试，最多 max_attempts 次，
            重试间隔按 backoffs 递增退避；
          - 非网络错误（仓库损坏、命令缺失等）快速失败不重试；
          - 每次尝试都记录尝试序号、退出码、耗时、输出摘要；
          - 最终结果一定明确：current / available / unknown（含重试次数与原因），
            不会静默失败。
        返回 (status, detail)：
          status = "current"   已是最新
                 | "available" 远端有新提交，可升级
                 | "unknown"    检查失败（重试耗尽或不可重试错误）
        注意：退出码不可靠（成功检查固定为 0，失败为 1，
        但"已是最新"与"有更新"两种结果都是 0），必须解析输出文本。
        """
        exe = which_anywhere(st.spec["cmd"].split()[0])
        if exe is None:
            if mod:
                self.logger.warn(mod, "更新状态检查：hermes 命令未找到，跳过")
            return "unknown", "hermes 命令未找到"
        if mod:
            self.logger.info(mod, "本次检查使用的更新分支",
                             branch=SETTINGS.hermes_branch,
                             note="main=稳定发布线（上游无独立 stable 分支）")
            if SETTINGS.proxy_enabled(st.spec["key"]):
                proxy, source = get_proxy()
                if proxy:
                    self.logger.info(mod, "hermes 网络通道：系统代理已启用，走代理访问 GitHub",
                                     proxy=proxy, source=source)
                else:
                    self.logger.warn(mod, "hermes 网络通道：系统代理已启用但未检测到代理，将直连 GitHub",
                                     suggestion="本机网络若需代理才能访问 GitHub，"
                                                "请开启系统代理（浏览器代理），"
                                                "或设置环境变量 WORKBENCH_PROXY=127.0.0.1:端口 后重启工作台")
            else:
                self.logger.info(mod, "hermes 网络通道：系统代理已禁用，直连访问 GitHub",
                                 suggestion="若直连失败，勾选 Hermes 卡片上的「系统代理」开关后重试")
        for attempt in range(1, max_attempts + 1):
            status, detail, retryable = self._update_check_once(st, exe, mod)
            if status != "unknown" or not retryable or attempt == max_attempts:
                if status != "unknown" and attempt > 1:
                    if mod:
                        self.logger.info(mod, f"更新状态检查在第 {attempt} 次尝试后成功",
                                         status=status)
                elif status == "unknown" and retryable:
                    reason = detail or "网络错误"
                    if mod:
                        self.logger.warn(mod,
                                         f"更新状态检查失败：已自动重试 {attempt} 次仍无法访问远端",
                                         cmd="hermes update --check",
                                         error=reason,
                                         suggestion="GitHub 连接不稳定（网络波动/代理）。"
                                                    "可稍后再点「检查更新」；"
                                                    "升级按钮仍可强制执行 hermes update")
                    detail = f"重试 {attempt} 次后仍失败：{reason}"
                return status, detail
            # 瞬时网络故障 → 退避后重试
            wait = backoffs[min(attempt - 1, len(backoffs) - 1)]
            if mod:
                self.logger.warn(mod, f"网络瞬时故障，{wait}s 后进行第 {attempt + 1} 次尝试"
                                 f"（最多 {max_attempts} 次）")
            time.sleep(wait)
        return "unknown", "检查失败"  # 理论上不可达

    def check_version(self, key, quiet=False):
        """在后台线程中检测当前版本与最新版本。"""
        st = self.services[key]
        if st.ver_checking:
            return
        if st.upgrading:
            # 升级会执行 git pull/重装，同时跑检查会与 git 操作争抢仓库锁
            self.logger.warn(f"{key}.version",
                             f"{st.spec['name']} 正在升级，跳过本次版本检查"
                             "（避免与升级的 git 操作并发冲突）")
            return
        st.ver_checking = True
        if not quiet:
            self.logger.info(f"{key}.version", f"正在检查 {st.spec['name']} 版本…")
        threading.Thread(target=self._version_worker, args=(key,), daemon=True).start()

    def check_all_versions(self):
        for key in self.services:
            self.check_version(key, quiet=True)

    def _version_worker(self, key):
        st = self.services[key]
        mod = f"{key}.version"
        t0 = time.time()
        try:
            self.logger.debug(mod, "版本检测流程开始")
            current = self._fetch_current_version(st, mod)
            extra = {}
            if st.spec.get("update_check"):
                # hermes：通过 `hermes update --check` 获取远端更新状态（含 git fetch，耗时较长）
                latest = None
                status, detail = self._fetch_update_availability(st, mod)
                extra = {"update_status": status, "update_detail": detail}
                self.logger.debug(mod, "更新状态检测完成", status=status, detail=detail or "-")
            elif st.spec["latest_cmd"] is not None:
                latest = self._fetch_latest_version(st, mod)
            else:
                latest = None
                self.logger.debug(mod, "该服务无远程最新版本可比对")
            self.logger.debug(mod, "版本检测流程结束", current=current or "未知",
                              latest=latest or "-", elapsed=dur(t0),
                              **({"update_status": extra["update_status"]} if extra else {}))
            self.ui_queue.put(("version", key, current, latest, extra))
        except Exception as e:  # 兜底：任何未预期异常都不能让线程静默死亡
            # 否则 ver_checking 永远为 True，界面会一直停留在「检测中…」
            import traceback
            self.logger.error(mod, "版本检测线程发生未预期异常",
                              error=repr(e), trace=traceback.format_exc(limit=5),
                              suggestion="请将 logs 目录下的日志反馈给开发者排查")
            self.ui_queue.put(("version", key, None, None, {}))

    def _upgrade_argv(self, st):
        """构造升级命令；返回 argv 或 None（不可用）。"""
        cmd = st.spec["upgrade_cmd"]
        if cmd is not None:
            if os.path.isfile(cmd[0]):
                return to_exec(cmd)
            return None
        # hermes：动态解析可执行文件后执行其 update 子命令；
        # --branch 固定更新通道（默认 main 稳定线，本地若在其他分支会自动切回）
        exe = which_anywhere(st.spec["cmd"].split()[0])
        if exe is None:
            return None
        return to_exec([exe, "update", "--branch", SETTINGS.hermes_branch])

    def upgrade_service(self, key):
        st = self.services[key]
        name = st.spec["name"]
        mod = f"{key}.upgrade"
        if st.upgrading:
            self.logger.warn(mod, f"忽略重复的升级请求：{name} 正在升级中")
            return
        if st.running:
            self.logger.error(mod, f"升级请求被拒绝：{name} 正在运行（PID {st.pid}）",
                              suggestion="先点击「停止」再升级；运行中替换文件会因占用而失败，"
                                         "且可能损坏安装")
            return
        argv = self._upgrade_argv(st)
        if argv is None:
            tool = st.spec["upgrade_cmd"][0] if st.spec["upgrade_cmd"] else "hermes 可执行文件"
            self.logger.error(mod, f"升级请求被拒绝：找不到 {name} 的升级工具", tool=tool,
                              suggestion="确认 npm/hermes 安装位置未变，或重新安装该服务")
            return
        st.upgrading = True
        self.refresh_all()
        self.logger.info(mod, f"收到升级请求", trigger="界面按钮",
                         current_version=st.version or "未知")
        threading.Thread(target=self._upgrade_worker, args=(key, argv), daemon=True).start()

    def _upgrade_worker(self, key, argv):
        """升级主流程：六阶段结构化执行，每阶段记录参数、耗时与结果。

        阶段划分：
          1/6 前置校验     —— 服务状态、升级环境（NODE_OPTIONS 剥离情况、超时上限）
          2/6 版本快照     —— 升级前当前版本 / 目标版本（用于升级后比对）
          3/6 执行升级命令 —— 子进程输出实时分级记录（npm warn/error 自动识别）
          4/6 完整性校验   —— 命令可解析、版本可读（专防"半升级"损坏状态）
          5/6 结果汇总     —— 成功/失败结论、失败原因、处理建议
          6/6 状态刷新     —— 重新检测版本并更新界面

        说明：本工作台设计为"升级前必须停止服务"，因此无自动重启阶段；
        升级成功后由用户手动点击「启动」。
        """
        st = self.services[key]
        name = st.spec["name"]
        mod = f"{key}.upgrade"
        log = self.logger
        t_total = time.time()
        log.info(mod, f"━━━ {name} 升级流程开始 ━━━", cmd=" ".join(argv))

        # ---- 阶段 1/6：前置校验 ----
        t0 = time.time()
        log.info(mod, "阶段 1/6 前置校验：服务状态与升级环境")
        if st.running:  # 双重保险：进入线程后再查一次
            log.error(mod, "阶段 1/6 失败：服务在升级开始时仍在运行", pid=st.pid,
                      suggestion="请先停止服务（运行中升级会因文件占用而失败并可能损坏安装）")
            self.ui_queue.put(("updone", key, None, None))
            return
        # 各服务按自身「系统代理」开关决定网络环境（hermes 默认开：GitHub 需代理）
        env = service_net_env(key, log, mod)
        log.debug(mod, "升级环境就绪", cwd=WORK_DIR,
                  node_options="已剥离（规避 safe-delete 拦截器在 junction 路径上崩溃）",
                  network="代理" if SETTINGS.proxy_enabled(key) else "直连",
                  timeout=f"{UPGRADE_TIMEOUT}s")
        # Node 环境兼容性预检（低位版本/32 位安装提前告警，不阻断执行）
        note = node_compat_note(st.spec)
        if note:
            log.warn(mod, f"阶段 1/6 Node 环境兼容性提示：{note}",
                     suggestion="该包对 Node 版本有要求（engines.node），低位环境升级"
                                "大概率报 EBADENGINE 或安装后无法运行；"
                                "建议先升级 Node.js（64 位 LTS）再升级此服务")
        if st.spec.get("update_check"):
            if SETTINGS.proxy_enabled(key):
                proxy, source = get_proxy()
                log.info(mod, "阶段 1/6 通过", elapsed=dur(t0),
                         branch=SETTINGS.hermes_branch,
                         network=proxy or "直连（开关开启但未检测到代理）",
                         proxy_source=source or "-")
            else:
                log.info(mod, "阶段 1/6 通过", elapsed=dur(t0),
                         branch=SETTINGS.hermes_branch, network="直连（系统代理已禁用）")
        else:
            log.info(mod, "阶段 1/6 通过", elapsed=dur(t0))

        # ---- 阶段 2/6：升级前版本快照（亦用于升级失败回滚） ----
        t0 = time.time()
        log.info(mod, "阶段 2/6 记录升级前版本快照（用于升级后比对与回滚）")
        before = self._fetch_current_version(st, mod)
        target = self._fetch_latest_version(st, mod) if st.spec["latest_cmd"] else None
        # 额外做回滚快照（版本快照 + hermes git commit）
        self._snapshot_before_upgrade(st, mod)
        log.info(mod, "阶段 2/6 完成", before=before or "未知",
                 target=target or "最新（git 自管理）", elapsed=dur(t0))

        # ---- 阶段 3/6：执行升级命令 ----
        t0 = time.time()
        log.info(mod, "阶段 3/6 启动升级子进程", cmd=" ".join(argv), cwd=WORK_DIR)
        try:
            proc = subprocess.Popen(argv, cwd=WORK_DIR, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True,
                                    creationflags=NO_WINDOW,
                                    encoding="utf-8", errors="replace", env=env)
        except OSError as e:
            log.error(mod, "阶段 3/6 失败：升级子进程无法启动", error=str(e),
                      errno=getattr(e, "winerror", getattr(e, "errno", None)),
                      suggestion="检查升级工具路径是否有效、磁盘空间是否充足",
                      trace=traceback.format_exc(limit=3))
            self.ui_queue.put(("updone", key, None, None))
            return
        log.debug(mod, "升级子进程已启动", pid=proc.pid)

        # 输出读取放独立线程：避免 npm 长时间无输出时阻塞读取循环，
        # 导致 wait 超时保护失效（旧版真实缺陷）
        n_lines = [0]
        out_tail = []  # 最近输出缓存（失败时用于识别 EBADENGINE 等错误特征）

        def _reader():
            for raw in proc.stdout:
                raw = raw.rstrip()
                if not raw:
                    continue
                n_lines[0] += 1
                out_tail.append(raw)
                if len(out_tail) > 400:  # 限制内存占用，只保留最近输出
                    del out_tail[:200]
                log.log(_npm_line_level(raw), mod, f"子进程输出：{raw}")

        reader = threading.Thread(target=_reader, daemon=True)
        reader.start()

        # 带截止时间的等待：真正生效的超时保护
        deadline = time.time() + UPGRADE_TIMEOUT
        while proc.poll() is None and time.time() < deadline:
            time.sleep(0.5)
        if proc.poll() is None:
            _kill_tree(proc.pid)  # 杀整个进程树，防止僵尸 node.exe 占用文件
            reader.join(timeout=5)
            log.error(mod, f"阶段 3/6 失败：升级超时（>{UPGRADE_TIMEOUT // 60} 分钟），"
                           f"已强制终止整个进程树", pid=proc.pid, elapsed=dur(t0),
                      suggestion="常见原因：网络不通/代理异常导致 npm 下载卡住。"
                                 "请在终端手动执行上述升级命令观察卡点，"
                                 "或检查 npm registry 配置后重试")
            self.ui_queue.put(("updone", key, None, None))
            return
        reader.join(timeout=5)
        rc = proc.returncode
        log.info(mod, "阶段 3/6 升级命令执行结束", exit_code=rc,
                 output_lines=n_lines[0], elapsed=dur(t0))

        # ---- 阶段 4/6：升级后完整性校验 ----
        t0 = time.time()
        log.info(mod, "阶段 4/6 完整性校验：命令可解析 + 新版本可读")
        exe = which_anywhere(st.spec["cmd"].split()[0])
        after = self._fetch_current_version(st, mod)
        broken = False
        if exe is None:
            log.error(mod, "阶段 4/6 完整性校验失败：升级后命令无法解析（启动脚本疑似丢失）",
                      elapsed=dur(t0),
                      suggestion="安装已损坏（历史案例：升级中途被杀导致 shim 丢失）。"
                                 "修复：① 结束残留 node.exe 进程；② 在终端执行 "
                                 "npm install -g <包名>@latest 干净重装；③ 重启工作台")
            broken = True
        else:
            log.debug(mod, "命令解析成功", exe=exe)
            if after is None:
                log.warn(mod, "命令存在但版本读取失败，安装可能不完整", exe=exe,
                         suggestion="建议手动执行 --version 确认；若异常则按安装损坏流程重装")
            else:
                log.debug(mod, "新版本读取成功", version=after)
        log.info(mod, "阶段 4/6 完成", integrity="损坏" if broken else "正常",
                 after=after or "未知", elapsed=dur(t0))

        # ---- 阶段 5/6：结果汇总 ----
        ok = (rc == 0) and not broken
        if ok:
            if before and after and ver_tuple(after) > ver_tuple(before):
                result = f"{before} → {after}"
            elif after:
                result = f"当前 {after}"
            else:
                result = "完成"
            log.success(mod, f"━━━ {name} 升级流程成功结束 ━━━", result=result,
                        total_elapsed=dur(t_total),
                        suggestion="如需使用请点击「启动」重新启动服务")
        else:
            reasons = []
            if rc != 0:
                reasons.append(f"升级命令退出码 {rc}")
            if broken:
                reasons.append("升级后命令无法解析（安装损坏）")
            log.error(mod, f"━━━ {name} 升级流程失败 ━━━", reason="；".join(reasons),
                      exit_code=rc, integrity="损坏" if broken else "未通过确认",
                      total_elapsed=dur(t_total),
                      rollback_available=str(st.rollback_available),
                      suggestion=f"升级失败后「回滚」按钮{'已' if st.rollback_available else '未'}可用，"
                                 f"可点击回滚还原到升级前版本")
            self._log_failure_hints(mod, key, rc, broken, output_text="\n".join(out_tail))

        # ---- 阶段 6/6：刷新版本状态 ----
        extra = {}
        if st.spec.get("update_check"):
            status, detail = self._fetch_update_availability(st, mod)
            extra = {"update_status": status, "update_detail": detail}
            latest = None
        else:
            latest = self._fetch_latest_version(st, mod) if st.spec["latest_cmd"] else None
        log.debug(mod, "阶段 6/6 版本状态已刷新", current=after or "未知",
                  latest=latest or "-",
                  **({"update_status": extra["update_status"]} if extra else {}))
        self.ui_queue.put(("updone", key, after, latest, extra))

    def _log_failure_hints(self, mod, key, rc, broken, output_text=""):
        """根据失败特征输出针对性排查建议（沉淀自历史真实案例）。

        output_text：升级子进程的最近输出（out_tail），用于识别
        EBADENGINE（Node 版本不满足包要求）等特定错误并给出定向建议。
        """
        hints = [
            f"打开日志文件查看完整现场：{LOG_FILE}",
            "在终端手动执行升级命令复现，观察具体报错（工作台已剥离 NODE_OPTIONS，"
            "终端环境若有差异需注意）",
        ]
        low = (output_text or "").lower()
        if ("ebadengine" in low or "unsupported engine" in low
                or ("engine" in low and "not compatible" in low)):
            ver, arch, _exe = node_env()
            arch_txt = f"，{arch_label(arch)}" if arch else ""
            hints.insert(0, f"Node 版本不满足该包的 engines 要求"
                            f"（当前 v{ver or '未知'}{arch_txt}）→ "
                            "请先升级 Node.js（建议 64 位 LTS）再升级此服务")
        if broken:
            hints.append("安装损坏（历史案例：升级中途被杀导致启动脚本丢失）→ "
                         "结束残留 node.exe 进程后，在终端执行 "
                         "npm install -g <包名>@latest 干净重装，再重启工作台")
        if rc != 0:
            hints.append("退出码非 0 → 重点查看上方 [ERROR] 级别的子进程输出："
                         "网络类错误（ETIMEDOUT/ECONNRESET）检查代理与 registry；"
                         "EPERM/EBUSY 表示文件被占用，确认服务已停止、"
                         "无残留 node.exe 进程后重试")
        if key == "hermes":
            hints.append("hermes 为 git 安装：可在其目录下 git status / git pull "
                         "确认仓库状态后重试 hermes update")
        for i, h in enumerate(hints, 1):
            self.logger.warn(mod, f"处理建议 {i}/{len(hints)}：{h}")

    # ---------------- 状态刷新 ----------------
    def _process_queue(self):
        while True:
            try:
                msg = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            kind = msg[0]
            if kind == "log":
                text = msg[1]
                tag = msg[2] if len(msg) > 2 else "info"
                to_file = msg[3] if len(msg) > 3 else True
                self.log_msg(text, tag, to_file=to_file)
            elif kind == "version":
                _, key, current, latest = msg[0], msg[1], msg[2], msg[3]
                extra = msg[4] if len(msg) > 4 else {}
                st = self.services[key]
                st.version, st.latest = current, latest
                st.update_status = extra.get("update_status")
                st.update_detail = extra.get("update_detail")
                st.ver_checking = False
                st.ver_checked = True
                name = st.spec["name"]
                mod = f"{key}.version"
                if current is None:
                    self.logger.error(mod, f"{name} 版本检测失败：无法获取当前版本",
                                      suggestion="确认命令已安装且 --version 可正常执行")
                elif st.spec.get("update_check"):
                    # hermes：按 `hermes update --check` 的结果报告
                    if st.update_status == "current":
                        self.logger.success(mod, f"{name} 当前版本 v{current}，已是最新（远端无新提交）")
                    elif st.update_status == "available":
                        self.logger.info(mod, f"{name} 当前版本 v{current}，"
                                         f"{st.update_detail or '远端有新提交'}，可升级")
                    else:
                        self.logger.warn(mod, f"{name} 当前版本 v{current}，但更新状态检查失败"
                                         f"（{st.update_detail or '原因未知'}）",
                                         suggestion="检查网络连通性后重新「检查更新」；"
                                                    "升级按钮仍可强制执行 hermes update")
                elif latest is not None:
                    if ver_tuple(latest) > ver_tuple(current):
                        self.logger.info(mod, f"{name} 当前 {current}，最新 {latest}，可升级")
                    else:
                        self.logger.success(mod, f"{name} 当前 {current}，已是最新版本")
                elif st.spec["latest_cmd"] is None:
                    self.logger.info(mod, f"{name} 当前版本 {current}（git 自管理，无远程版本可比）")
                else:
                    self.logger.warn(mod, f"{name} 当前版本 {current}，但最新版本获取失败",
                                     suggestion="检查网络/代理或 npm registry 配置，"
                                                "稍后重新「检查更新」")
            elif kind == "updone":
                _, key, current, latest = msg[0], msg[1], msg[2], msg[3]
                extra = msg[4] if len(msg) > 4 else {}
                st = self.services[key]
                st.version, st.latest = current, latest
                st.update_status = extra.get("update_status")
                st.update_detail = extra.get("update_detail")
                st.upgrading = False
                st.ver_checked = True

    def refresh_all(self):
        self._process_queue()
        # 代理/分支状态标签：仅在设置变化后刷新（避免周期性代理探测卡界面）
        if self._net_dirty:
            self._refresh_net_labels()
            self._net_dirty = False

        # 延迟启动资源监控（确保一轮 refresh 后才启动线程）
        if _PSUTIL_AVAIL and not self._res_monitor_started:
            self._start_resource_monitor()

        for key, st in self.services.items():
            # 检测外部退出（崩溃自愈看门狗）
            if st.proc is not None and st.proc.poll() is not None:
                code = st.proc.returncode
                # 由看门狗接管崩溃处理（日志和重启）
                self.log_msg(f"{st.spec['name']} 进程已退出（退出码 {code}），看门狗正在处理…", "err")
                self._watchdog_check(key)

            # 处理 STATUS_CRASHED 状态（看门狗设置后，刷新行为由 watchdog 管理）
            color, text = COLORS.get(st.status, COLORS[STATUS_STOPPED])
            c = self.cards[key]
            c["dot"].configure(text="●", fg=color)
            c["status"].configure(text=text, fg=color)

            # PID / 运行时长 / 资源监控
            if st.running:
                pid_text = f"PID：{st.pid}    运行时长：{st.uptime_text()}"
                c["info"].configure(text=pid_text)
                c["start"].configure(state="disabled")
                c["stop"].configure(state="normal")
                c["restart"].configure(state="normal")
                # 资源监控行
                if _PSUTIL_AVAIL:
                    res_text = st.resource_text()
                    threshold_warn = self._check_resource_threshold(key)
                    if threshold_warn:
                        res_text += f"  ⚠️ {threshold_warn}"
                        c["res"].configure(text=res_text, fg="#d97706")
                    else:
                        c["res"].configure(text=res_text, fg="#374151")
                else:
                    c["res"].configure(text="（资源监控：需安装 psutil）", fg="#9ca3af")
            else:
                c["info"].configure(text="PID：-    运行时长：-")
                c["start"].configure(state="normal")
                c["stop"].configure(state="disabled")
                c["restart"].configure(state="disabled" if st.status == STATUS_MISSING else "normal")
                c["res"].configure(text="", fg="#9ca3af")

            # 版本行
            if st.upgrading:
                c["ver"].configure(text="版本：升级中，请勿关闭工作台…", fg="#d97706")
            elif st.ver_checking:
                c["ver"].configure(text="版本：检测中…", fg="#6b7280")
            elif not st.ver_checked or st.version is None:
                c["ver"].configure(text="版本：-", fg="#6b7280")
            elif st.spec.get("update_check"):
                # hermes：按 `hermes update --check` 的三态结果渲染
                if st.update_status == "current":
                    c["ver"].configure(text=f"版本：v{st.version}（已是最新）", fg="#16a34a")
                elif st.update_status == "available":
                    detail = f"，{st.update_detail}" if st.update_detail else ""
                    c["ver"].configure(
                        text=f"版本：v{st.version} → 远端有更新{detail}，可升级",
                        fg="#d97706")
                else:
                    c["ver"].configure(
                        text=f"版本：v{st.version}（更新状态未知，升级执行 hermes update）",
                        fg="#6b7280")
            elif st.latest is None:
                c["ver"].configure(text=f"版本：{st.version}（最新版本获取失败）", fg="#6b7280")
            elif st.has_update:
                c["ver"].configure(text=f"版本：{st.version} → 最新 {st.latest}，可升级",
                                   fg="#d97706")
            else:
                c["ver"].configure(text=f"版本：{st.version}（已是最新）", fg="#16a34a")

            # 升级 / 检查更新 / 回滚按钮
            can_upgrade = (not st.running) and (not st.upgrading) and (
                st.spec["latest_cmd"] is None or st.has_update)
            c["upgrade"].configure(state="normal" if can_upgrade else "disabled")
            c["check"].configure(state="disabled" if (st.ver_checking or st.upgrading) else "normal")
            # 回滚按钮：仅当有回滚快照 && 服务已停止 && 未在升级中时可用
            can_rollback = st.rollback_available and not st.running and not st.upgrading
            c["rollback"].configure(state="normal" if can_rollback else "disabled")

        # 刷新托盘图标（运行服务数量变化时）
        if self._tray_running:
            self._update_tray_icon()

        self.root.after(self.REFRESH_MS, self.refresh_all)

    # ---------------- 退出（兼容托盘模式） ----------------
    def _on_close_tray_aware(self):
        """关闭窗口时的处理：如果托盘在运行则最小化到托盘，否则提示退出。"""
        if self._tray_running and self._tray_icon is not None:
            self.root.withdraw()
            self.log_msg("工作台已最小化到系统托盘（右下角图标）", "info")
            return
        self.on_close()

    def on_close(self):
        upgrading = [st.spec["name"] for st in self.services.values() if st.upgrading]
        if upgrading:
            messagebox.showwarning("升级进行中", "以下服务正在升级，请等待完成后再关闭：\n  "
                                    + "\n  ".join(upgrading))
            return
        running = [st.spec["name"] for st in self.services.values() if st.running]
        if running:
            answer = messagebox.askyesnocancel(
                "退出确认",
                "以下服务仍在运行：\n  " + "\n  ".join(running) +
                "\n\n是：停止所有服务并退出\n否：保持服务运行，仅关闭工作台\n取消：不退出"
            )
            if answer is None:
                return
            if answer:
                self.stop_all()
        self._stop_tray()
        self.root.destroy()

    # ================ 系统托盘（后台模式） ================
    def _create_tray_image(self):
        """生成托盘图标：当前运行服务数量 + 状态色。"""
        size = 64
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        # 背景圆
        running_count = sum(1 for st in self.services.values() if st.running)
        color = (22, 163, 74) if running_count > 0 else (107, 114, 128)
        draw.ellipse([4, 4, size - 4, size - 4], fill=(*color, 230))
        # 文字
        try:
            font = ImageFont.truetype("segoeui.ttf", 28)
        except Exception:
            font = ImageFont.load_default()
        text = str(running_count)
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(((size - tw) / 2, (size - th) / 2 - 2), text,
                  fill="white", font=font)
        return img

    def _tray_menu(self):
        """构造系统托盘右键菜单。"""
        menu_items = []
        # 各服务状态
        for key, st in self.services.items():
            status = "运行中" if st.running else "已停止"
            menu_items.append(
                pystray.MenuItem(
                    f"{st.spec['name']}: {status}",
                    # action 回调实际签名 action(icon, menu_item)（两个位置参数），
                    # 用 *args 吃掉传入参数，k 用关键字默认参数正确捕获 key
                    lambda *args, k=key: self._tray_toggle_service(k),
                    # checked 回调签名 checked(menu_item)（一个位置参数），
                    # 用 _ 接收，k 用关键字默认参数正确捕获 key
                    checked=lambda _, k=key: self.services[k].running))
        menu_items.append(pystray.Menu.SEPARATOR)
        menu_items.append(
            pystray.MenuItem(
                "全部启动",
                # pystray 回调签名 action(icon, menu_item)，用 *args 兼容任意传入参数
                lambda *args: self._tray_action(self.start_all)))
        menu_items.append(
            pystray.MenuItem(
                "全部停止",
                lambda *args: self._tray_action(self.stop_all)))
        menu_items.append(pystray.Menu.SEPARATOR)
        menu_items.append(
            pystray.MenuItem(
                "显示窗口",
                lambda *args: self.root.after(0, self._tray_show_window)))
        menu_items.append(
            pystray.MenuItem(
                "退出工作台",
                lambda *args: self.root.after(0, self._tray_quit)))
        return pystray.Menu(*menu_items)

    def _tray_toggle_service(self, key):
        """托盘菜单中切换服务的启停。"""
        st = self.services[key]
        if st.running:
            self.root.after(0, lambda: self.stop_service(key))
        else:
            self.root.after(0, lambda: self.start_service(key))

    def _tray_action(self, fn):
        """在 tk 主线程执行批量操作。"""
        self.root.after(0, fn)

    def _tray_show_window(self):
        """从托盘恢复窗口。"""
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _tray_quit(self):
        """从托盘完全退出。"""
        self._tray_running = False
        self.root.after(0, self.on_close)

    def toggle_tray_mode(self):
        """点击「托盘运行」按钮：最小化到系统托盘。"""
        if not _PYSTRAY_AVAIL:
            return
        if self._tray_running:
            self.log_msg("托盘已在运行中", "info")
            return
        # 启动托盘线程
        def tray_loop(icon):
            icon.run()

        # 首次创建托盘图标
        img = self._create_tray_image()
        menu = self._tray_menu()
        self._tray_icon = pystray.Icon(
            "workbench_tray", img, "AI agent管理工作台", menu)
        self._tray_running = True
        threading.Thread(target=tray_loop,
                         args=(self._tray_icon,), daemon=True).start()
        self.root.withdraw()
        self.log_msg("已最小化到系统托盘（右下角图标），右键可操作服务", "ok",
                      to_file=True)

    def _stop_tray(self):
        """停止系统托盘。"""
        if self._tray_icon is not None:
            try:
                self._tray_icon.stop()
            except Exception:
                pass
            self._tray_icon = None
            self._tray_running = False

    def _update_tray_icon(self):
        """刷新托盘图标（运行服务数量变化时）。"""
        if self._tray_running and self._tray_icon is not None:
            try:
                img = self._create_tray_image()
                self._tray_icon.icon = img
                self._tray_icon.menu = self._tray_menu()
            except Exception:
                pass

    # ================ 资源监控（CPU/内存，psutil 驱动） ================
    def _start_resource_monitor(self):
        """启动后台资源监控线程（仅在 psutil 可用时）。"""
        if not _PSUTIL_AVAIL or self._res_monitor_started:
            return
        self._res_monitor_started = True
        threading.Thread(target=self._resource_monitor_loop, daemon=True).start()

    def _resource_monitor_loop(self):
        """后台线程：每秒采集各服务进程的 CPU/内存（仅对运行中的服务）。"""
        while True:
            try:
                for key, st in self.services.items():
                    if not st.running or st.proc is None:
                        continue
                    pid = st.pid
                    if pid is None:
                        continue
                    try:
                        proc = psutil.Process(pid)
                        # 采集进程树的总资源
                        total_cpu = 0.0
                        total_mem = 0
                        try:
                            total_cpu = proc.cpu_percent(interval=0.0)
                            total_mem = proc.memory_info().rss
                            # 包含子进程
                            for child in proc.children(recursive=True):
                                try:
                                    total_cpu += child.cpu_percent(interval=0.0)
                                    total_mem += child.memory_info().rss
                                except (psutil.NoSuchProcess, psutil.AccessDenied):
                                    pass
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            continue
                        st.cpu_percent = total_cpu
                        st.rss_bytes = total_mem
                        st.memory_mb = total_mem / (1024 * 1024)
                        # 保留最近 30 个采样点
                        st.resource_history.append((time.time(), total_cpu, total_mem))
                        if len(st.resource_history) > 30:
                            st.resource_history.pop(0)
                    except (psutil.NoSuchProcess, psutil.AccessDenied, AttributeError):
                        pass
            except Exception:
                pass
            time.sleep(1.0)

    def _check_resource_threshold(self, key):
        """检查资源阈值，返回告警文本或 None。"""
        st = self.services[key]
        if not st.running:
            return None
        warnings = []
        if st.memory_mb > 2048:
            warnings.append(f"内存 {st.memory_mb:.0f}MB 超过 2GB")
        if st.cpu_percent > 80:
            warnings.append(f"CPU {st.cpu_percent:.0f}% 持续高位")
        return "；".join(warnings) if warnings else None

    # ================ 崩溃自愈（看门狗） ================
    def _watchdog_check(self, key):
        """检查服务是否意外退出，若是则自增崩溃计数并尝试自动重启。"""
        st = self.services[key]
        if st.running or st.upgrading or st.status == STATUS_MISSING:
            return
        if st.pid is None:
            return
        # 仅在进程确实退出且不是由 stop_service 主动停止时计入崩溃
        if st.proc is not None and st.proc.poll() is not None:
            exit_code = st.proc.returncode
            st.last_exit_code = exit_code
            # 更新状态为崩溃
            st.status = STATUS_CRASHED
            # 只在看门狗开启且进程非正常退出(exit_code ≠ 0 或没有主动停止标记)时自增崩溃计数
            # 使用 lock 防止并发
            with self._watchdog_lock:
                st.crash_count += 1
                st.last_crash_time = time.time()
            self.logger.warn(f"{key}.watchdog",
                             f"{st.spec['name']} 意外退出（退出码 {exit_code}）"
                             f"，连续崩溃 {st.crash_count} 次",
                             suggestion="看门狗将自动尝试重启")
            # 清理旧的 proc 对象
            st.proc = None
            st.pid = None
            st.start_time = None
            # 自动重启逻辑（指数退避）
            if st.watchdog_enabled and st.crash_count <= 5:
                backoff = min(3 * (2 ** (st.crash_count - 1)), 30)
                self.logger.info(f"{key}.watchdog",
                                 f"{backoff}s 后自动重启 {st.spec['name']}（第 {st.crash_count} 次尝试）")
                # 在后台线程中等待退避后重启
                threading.Thread(
                    target=self._watchdog_restart,
                    args=(key, backoff, st.crash_count),
                    daemon=True).start()
            elif st.crash_count > 5:
                st.watchdog_enabled = False
                self.logger.error(f"{key}.watchdog",
                                  f"{st.spec['name']} 连续崩溃 {st.crash_count} 次，看门狗已自动停止"
                                  f"（防止无限重启循环）",
                                  suggestion="请手动检查日志排查崩溃原因，确认修复后点击「启动」")

    def _watchdog_restart(self, key, delay, expected_count):
        """等待退避时间后尝试重启，仅在崩溃计数未变时执行（防止重复调度）。"""
        time.sleep(delay)
        st = self.services[key]
        # 双重保险：确认崩溃计数未被其他线程消耗
        with self._watchdog_lock:
            if st.crash_count != expected_count or st.running or st.upgrading:
                return
        # 调用 start_service（已在主线程安全调用）
        self.root.after(0, lambda: self._do_watchdog_restart(key))

    def _do_watchdog_restart(self, key):
        """在主线程执行看门狗重启。"""
        st = self.services[key]
        if st.running or st.upgrading:
            return
        ok = self.start_service(key, quiet=True)
        if ok:
            self.logger.success(f"{key}.watchdog",
                                f"{st.spec['name']} 已由看门狗自动重启（连续崩溃 {st.crash_count} 次后恢复）")
            # 重启成功后可重置崩溃计数
            st.crash_count = 0
        else:
            self.logger.error(f"{key}.watchdog",
                              f"{st.spec['name']} 看门狗自动重启失败")

    # ================ 升级前备份 & 失败回滚 ================
    def _snapshot_before_upgrade(self, st, mod):
        """升级前做版本快照：npm 服务记录当前版本号，hermes 记录 git HEAD。
        返回快照描述（成功/失败）。"""
        st.pre_upgrade_version = self._fetch_current_version(st, mod)
        st.pre_upgrade_commit = None
        st.rollback_available = False

        if st.spec.get("update_check"):
            # hermes：记录 git HEAD
            exe = which_anywhere(st.spec["cmd"].split()[0])
            if exe:
                # 找到 hermes 安装目录下的 .git
                hermes_dir = os.path.dirname(os.path.dirname(exe))  # 多级尝试
                # 尝试从 hermes --version 获取安装路径
                try:
                    r = subprocess.run(
                        to_exec([exe, "version", "--path"]),
                        capture_output=True, text=True, timeout=30,
                        creationflags=NO_WINDOW, encoding="utf-8", errors="replace")
                    hermes_dir = (r.stdout or "").strip()
                except Exception:
                    pass
                # 试 git rev-parse
                for attempt_dir in [hermes_dir, os.path.join(hermes_dir, "..")]:
                    if not attempt_dir or not os.path.isdir(attempt_dir):
                        continue
                    try:
                        r = subprocess.run(
                            ["git", "rev-parse", "HEAD"],
                            cwd=attempt_dir, capture_output=True, text=True,
                            timeout=30, creationflags=NO_WINDOW)
                        if r.returncode == 0 and r.stdout.strip():
                            st.pre_upgrade_commit = r.stdout.strip()
                            break
                    except Exception:
                        continue
        else:
            # npm 服务：当前版本已记录在 st.pre_upgrade_version
            pass

        if st.pre_upgrade_version or st.pre_upgrade_commit:
            desc = f"版本 {st.pre_upgrade_version}" if st.pre_upgrade_version else ""
            if st.pre_upgrade_commit:
                desc += (f"，commit {st.pre_upgrade_commit[:12]}" if desc
                         else f"commit {st.pre_upgrade_commit[:12]}")
            self.logger.info(mod, f"升级前快照已记录：{desc}")
            st.rollback_available = True
        else:
            self.logger.warn(mod, "升级前快照未成功记录（版本解析失败），将无法回滚",
                             suggestion="若升级失败，需手动重新安装")
        return st.pre_upgrade_version

    def rollback_service(self, key):
        """回滚到升级前的版本。"""
        st = self.services[key]
        name = st.spec["name"]
        mod = f"{key}.rollback"

        if not st.rollback_available or (not st.pre_upgrade_version and not st.pre_upgrade_commit):
            self.logger.warn(mod, f"{name} 无可用回滚快照（未曾升级或快照未记录）",
                             suggestion="暂时无法回滚，可手动安装旧版本")
            return
        if st.running:
            self.logger.error(mod, f"回滚被拒绝：{name} 正在运行中",
                              suggestion="先停止服务再执行回滚")
            return
        if st.upgrading:
            self.logger.warn(mod, f"{name} 正在升级中，请等待完成",
                             suggestion="升级完成后如失败可执行回滚")
            return

        self.logger.info(mod, f"开始回滚 {name}…")
        threading.Thread(target=self._rollback_worker, args=(key,), daemon=True).start()

    def _rollback_worker(self, key):
        """回滚工作线程。"""
        st = self.services[key]
        name = st.spec["name"]
        mod = f"{key}.rollback"
        log = self.logger

        log.info(mod, f"━━━ {name} 回滚流程开始 ━━━")

        if st.spec.get("update_check"):
            # hermes：git checkout 回滚
            if not st.pre_upgrade_commit:
                log.error(mod, "Hermes 回滚失败：无 git commit 快照",
                          suggestion="快照可能未被记录，请手动进入 hermes 目录 git checkout 旧版本")
                self.ui_queue.put(("updone", key, None, None, {}))
                return
            try:
                # 找到 hermes 仓库目录
                exe = which_anywhere(st.spec["cmd"].split()[0])
                hermes_dir = None
                if exe:
                    try:
                        r = subprocess.run(
                            to_exec([exe, "version", "--path"]),
                            capture_output=True, text=True, timeout=30,
                            creationflags=NO_WINDOW, encoding="utf-8", errors="replace")
                        hermes_dir = (r.stdout or "").strip()
                    except Exception:
                        hermes_dir = os.path.dirname(os.path.dirname(exe))
                if not hermes_dir or not os.path.isdir(hermes_dir):
                    log.error(mod, "Hermes 回滚失败：无法确定仓库目录")
                    self.ui_queue.put(("updone", key, None, None, {}))
                    return

                r = subprocess.run(
                    ["git", "checkout", st.pre_upgrade_commit],
                    cwd=hermes_dir, capture_output=True, text=True,
                    timeout=60, creationflags=NO_WINDOW)
                if r.returncode != 0:
                    log.error(mod, "Hermes 回滚失败：git checkout 执行异常",
                              error=(r.stderr or "").strip()[:200],
                              suggestion="手动进入 hermes 安装目录执行 git checkout")
                    self.ui_queue.put(("updone", key, None, None, {}))
                    return
                log.success(mod, "Hermes git checkout 回滚成功",
                            commit=st.pre_upgrade_commit[:12])
            except Exception as e:
                log.error(mod, "Hermes 回滚异常", error=str(e))
                self.ui_queue.put(("updone", key, None, None, {}))
                return
        else:
            # npm 服务：npm install -g <pkg>@<旧版本>
            if not st.pre_upgrade_version:
                log.error(mod, f"{name} 回滚失败：无升级前版本号",
                          suggestion="快照可能未被记录，请手动 npm install -g <包名>@旧版本")
                self.ui_queue.put(("updone", key, None, None, {}))
                return
            upgrade_cmd = st.spec.get("upgrade_cmd")
            if not upgrade_cmd or not os.path.isfile(upgrade_cmd[0]):
                log.error(mod, "回滚失败：npm 路径无效",
                          suggestion="请检查 npm 配置路径是否正确")
                self.ui_queue.put(("updone", key, None, None, {}))
                return
            npm_path = upgrade_cmd[0]
            pkg_name = upgrade_cmd[-1] if upgrade_cmd[-1].startswith("@") else ""
            if not pkg_name:
                # 从升级命令中提取包名
                for part in upgrade_cmd:
                    if "@" in part and not part.startswith("-"):
                        pkg_name = part.split("@")[0]
                        break
            if not pkg_name:
                log.error(mod, "回滚失败：无法从升级命令解析包名",
                          suggestion="请手动执行 npm install -g <包名>@旧版本")
                self.ui_queue.put(("updone", key, None, None, {}))
                return

            rollback_argv = to_exec([npm_path, "install", "-g",
                                    f"{pkg_name}@{st.pre_upgrade_version}"])
            log.info(mod, f"执行 npm install -g {pkg_name}@{st.pre_upgrade_version}")
            try:
                proc = subprocess.Popen(
                    rollback_argv, capture_output=True, text=True,
                    creationflags=NO_WINDOW, encoding="utf-8", errors="replace",
                    env=service_net_env(key, log, mod))
                stdout, stderr = proc.communicate(timeout=300)
                if proc.returncode != 0:
                    log.error(mod, "npm 回滚安装失败",
                              exit_code=proc.returncode,
                              error=(stderr or stdout or "").strip()[:200])
                    self.ui_queue.put(("updone", key, None, None, {}))
                    return
                log.success(mod, f"npm 回滚安装成功：{st.pre_upgrade_version}")
            except subprocess.TimeoutExpired:
                log.error(mod, "npm 回滚安装超时")
                self.ui_queue.put(("updone", key, None, None, {}))
                return
            except Exception as e:
                log.error(mod, "npm 回滚安装异常", error=str(e))
                self.ui_queue.put(("updone", key, None, None, {}))
                return

        # 回滚成功后验证版本
        after = self._fetch_current_version(st, mod)
        log.info(mod, f"回滚后版本：{after or '未知'}")
        st.pre_upgrade_version = None
        st.pre_upgrade_commit = None
        st.rollback_available = False
        self.ui_queue.put(("updone", key, after, None, {}))
        self.logger.success(mod, f"━━━ {name} 回滚流程成功结束 ━━━",
                            version=after or "未知",
                            suggestion="如需使用请点击「启动」重新启动服务")


def main():
    root = tk.Tk()
    app = WorkbenchApp(root)
    app.log_msg("工作台已就绪。服务启动后将各自打开独立控制台窗口，可直接交互。", "info")
    app.log_msg(f"工作目录：{WORK_DIR}（来源：{WORK_DIR_SOURCE}；"
                f"顶栏「切换目录」可直接切换并持久化）", "info")
    # Node 环境摘要：版本/架构/npm 探测结果（低位版本与 32 位安装兼容提示）
    nver, narch, nexe = node_env()
    if nver:
        arch_txt = f"，{arch_label(narch)}" if narch else ""
        app.log_msg(f"Node 环境：v{nver}{arch_txt}（{nexe}）", "ok")
    else:
        app.log_msg("Node 环境：未检测到 node.exe——npm 类服务（claude/codex/pi）的"
                    "版本查询与升级不可用；服务启动不受影响（走 PATH/fnm 解析）", "warn")
    npm_ok = SYSTEM_NPM_SOURCE != "未找到"
    app.log_msg(f"系统 npm（claude/codex 用）：{SYSTEM_NPM or '未找到'}"
                f"（来源：{SYSTEM_NPM_SOURCE}）", "info" if npm_ok else "warn")
    fnm_ok = FNM_NPM_SOURCE != "未找到"
    app.log_msg(f"pi 用 npm：{FNM_NPM or '未找到'}（来源：{FNM_NPM_SOURCE}）",
                "info" if fnm_ok else "warn")
    for st in app.services.values():
        note = node_compat_note(st.spec)
        if note:
            app.log_msg(f"兼容性提示：{st.spec['name']} — {note}；该服务的升级/运行"
                        f"可能失败，建议升级 Node.js（64 位 LTS）", "warn")
    proxy, source = get_proxy()
    if proxy:
        app.log_msg(f"网络代理：{proxy}（来源：{source}）", "ok")
    else:
        app.log_msg("网络代理：未检测到（启用代理开关的服务将直连；"
                    "可开启系统/浏览器代理，或设置 WORKBENCH_PROXY 环境变量后重启工作台）",
                    "warn")
    # 各服务代理开关摘要（持久化配置）
    states = "，".join(
        f"{st.spec['name']}：{'开' if SETTINGS.proxy_enabled(k) else '关'}"
        for k, st in app.services.items())
    app.log_msg(f"服务代理开关（已持久化，点击卡片「系统代理」可切换）：{states}", "info")
    app.log_msg(f"hermes 更新分支：{SETTINGS.hermes_branch}（稳定版；上游无独立 stable 分支，"
                f"main 即官方发布线；卡片「更新分支」按钮可修改）", "info")
    app.log_msg(f"日志文件：{LOG_FILE}（界面按 {app.logger.level_name} 级别过滤，"
                f"文件记录全量，可在日志区切换级别）", "info")
    missing = []
    for st in app.services.values():
        if which_anywhere(st.spec["cmd"].split()[0]) is None:
            missing.append(st.spec["cmd"].split()[0])
    if missing:
        app.log_msg("提示：以下命令当前未在 PATH 中找到：" + "、".join(missing) +
                    "。安装并加入环境变量后即可在工作台启动。", "err")
    if os.environ.get("WORKBENCH_SMOKE") == "1":
        root.after(1500, root.destroy)  # 冒烟测试：自动关闭
    root.mainloop()


if __name__ == "__main__":
    main()
