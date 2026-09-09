# AI Agent 工作台 (AI Agent Workbench)

> 便携的 AI Agent 服务管理工作台，目前支持 claude、codex、hermes、pi 的服务管理。
> A portable workbench for managing local AI agent services: claude, codex, hermes, and pi.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](#%E7%B3%BB%E7%BB%9F%E8%A6%81%E6%B1%82)
[![Platform](https://img.shields.io/badge/platform-Windows%207%2F8%2F10%2F11-lightgrey.svg)](#%E7%B3%BB%E7%BB%9F%E8%A6%81%E6%B1%82)
[![CI](https://github.com/ROSSINHU/ai-agent-workbench/actions/workflows/lint.yml/badge.svg)](../../actions)

[English](#english) | [中文](#中文)

---

## 中文

### 简介

AI Agent 工作台是一个 Python + Tkinter 桌面应用，统一管理本地四个 AI Agent 命令行服务：`claude`（Anthropic Claude Code）、`hermes`（NousResearch hermes-agent）、`codex`（OpenAI codex-cli）、`pi`（earendil pi-coding-agent）。一个窗口解决「四个服务各自一套 CLI 升级、启动、代理配置」带来的散乱问题。

### 特性

- **四服务统一管理**：每张卡片独立显示版本、状态、PID、运行时长、退出码、运行日志。
- **一键版本检查 + 一键升级**：六阶段结构化升级流程，每阶段记录参数与耗时；失败时输出针对性建议（`EBADENGINE`、文件锁、磁盘空间、网络中断等）。
- **每服务系统代理开关**：独立控制每个服务的检查/升级/运行是否走代理；关闭时主动清除代理环境变量，避免工作台所在会话的代理污染子进程。
- **工作目录可切换**：顶栏「切换目录」按钮实时切换，持久化到 `workbench_settings.json` + `workbench2.cfg`；已运行服务重启后切换。
- **Node.js 环境兼容**：自动探测 64 位 `Program Files`、32 位 `Program Files (x86)`、per-user `LOCALAPPDATA\Programs\nodejs`、nvm symlink、fnm 默认别名等各类 npm 安装位置；启动时检查 Node 版本与架构（`x64` / `ia32` / `arm64`）是否满足各服务包 `engines.node` 要求。
- **跨机器便携**：内置 `AI agent管理工作台.bat` 配置向导，首次运行自动探测 Python / Node / fnm / 工作目录并写入 `workbench2.cfg`；拷贝到新 Windows 机器只需重跑 bat 重新探测。
- **结构化日志系统**：双通道（界面日志区按级别过滤 + 磁盘全量日志文件）便于事后排查；DEBUG 级别记录子进程原始输出、参数细节。
- **可选系统托盘图标与资源监控**：`pystray` / `Pillow` / `psutil` 依赖缺失时优雅降级，不影响主功能。

### 系统要求

- **操作系统**：Windows 10 / 11（32 位与 64 位均支持）
- **Python**：3.10+
- **Node.js**：22.0.0+（`claude` 与 `pi` 强制要求；建议安装 LTS）
  - 32 位 Windows 用户请从 [nodejs.org](https://nodejs.org) 下载 **Windows 32-bit Installer**
  - 推荐用 [fnm](https://github.com/Schniz/fnm) 或 [nvm-windows](https://github.com/coreybutler/nvm-windows) 多版本管理
- **磁盘空间**：约 200 MB（含可选依赖）

### 安装

1. **克隆仓库**：
   ```bash
   git clone https://github.com/ROSSINHU/ai-agent-workbench.git
   cd ai-agent-workbench
   ```
2. **安装 Python 依赖**（可选功能；不装也能跑主功能）：
   ```bash
   pip install -r requirements.txt
   ```
3. **安装要管理的 AI 服务**（按需）：
   ```bash
   npm install -g @anthropic-ai/claude-code@latest
   npm install -g @openai/codex@latest
   npm install -g @earendil-works/pi-coding-agent@latest
   # hermes 用 git 安装方式，参见官方仓库 https://github.com/NousResearch/hermes-agent
   ```
4. **首次启动**：双击 `AI agent管理工作台.bat`，按向导选择工作目录、确认 npm 路径、Python 解释器。

### 使用

- **启动服务**：在对应服务卡片上点「启动」
- **升级服务**：点「升级」；当前版本 vs 最新版本会实时显示在卡片上
- **切换代理**：每张卡片上有「系统代理」开关，修改后立即对检查/升级命令生效；运行中的服务需重启
- **切换工作目录**：顶栏「切换目录」选择新目录
- **查看日志**：界面日志区可切换级别（DEBUG / INFO / WARN / ERROR），全量日志在 `logs/workbench-YYYYMMDD.log`

### 配置

| 配置文件 | 用途 | 优先级 |
|---|---|---|
| `workbench2.cfg` | bat 向导生成的运行时配置（Python / npm / fnm / 工作目录） | 中 |
| `workbench_settings.json` | UI 内变更的工作目录、代理开关、hermes 更新分支 | 高 |
| `WORKBENCH_DIR` / `WORKBENCH_SYSTEM_NPM` / `WORKBENCH_FNM_NPM` 环境变量 | bat 显式传入 | 最高 |

> ⚠ `workbench2.cfg` 与 `workbench_settings.json` 包含个人路径，已加入 `.gitignore` —— 你的本地副本不会被 git 追踪。
> 参考模板：[`workbench2.cfg.example`](workbench2.cfg.example) / [`workbench_settings.json.example`](workbench_settings.json.example)

### 项目结构

```
service_workbench/
├── AI agent管理工作台.bat     # 便携启动器 / 配置向导
├── service_manager.py          # 启动入口（薄封装，bat 仍启动它）
├── workbench/                  # 分层实现
│   ├── core.py                 # headless 核心逻辑（配置/进程/代理/Node/日志/状态，无 GUI 依赖）
│   ├── platform_windows.py     # Windows 平台特定能力（注册表系统代理）
│   └── app.py                  # Tkinter 图形界面（WorkbenchApp + main）
├── requirements.txt            # 可选 Python 依赖
├── AIWorkbench.spec            # PyInstaller 打包配置（构建单文件 exe）
├── workbench2.cfg.example      # 启动器配置模板
├── workbench_settings.json.example  # UI 运行时配置模板
├── LICENSE                     # MIT
├── README.md                   # 本文件
├── CHANGELOG.md                # 版本变更日志
├── CONTRIBUTING.md             # 贡献指南
├── .gitignore
├── .gitattributes              # 强制 .bat CRLF / 其他 LF
└── .github/workflows/lint.yml  # CI：多 Python 版本编译检查
```

分层依赖单向无环：`app → core → platform_windows`。`core.py` 不依赖
Tkinter，可被命令行、自动化测试或未来的其他界面复用。

### 从源码运行

```bash
pip install -r requirements.txt   # 仅托盘/资源监控需要，核心功能零第三方依赖
python service_manager.py         # 或双击 AI agent管理工作台.bat
```

### 构建免安装 exe（可选）

不想让最终用户安装 Python 时，可用 PyInstaller 打成单文件：

```bash
pip install pyinstaller psutil pystray Pillow
pyinstaller AIWorkbench.spec --noconfirm --clean
# 产物：dist/AIWorkbench.exe（约 21 MB，双击即运行）
```

把 `AIWorkbench.exe` 单独拷到任意目录即可运行；`workbench2.cfg`、
`workbench_settings.json` 与 `logs/` 会就近生成在 exe 所在目录，便于便携迁移。

### 贡献

欢迎贡献！请阅读 [CONTRIBUTING.md](CONTRIBUTING.md) 了解分支策略、提交规范与代码风格。

### 许可证

本项目以 [MIT License](LICENSE) 发布。

### 致谢

灵感来自对 claude / hermes / codex / pi 四个 CLI 工具的日常管理需求。

---

## English

### Overview

**AI Agent Workbench** is a Python + Tkinter desktop application that unifies the management of four local AI agent CLIs: `claude` (Anthropic Claude Code), `hermes` (NousResearch hermes-agent), `codex` (OpenAI codex-cli), and `pi` (earendil pi-coding-agent). One window replaces four scattered CLI upgrade / start / proxy configurations.

### Features

- **Unified 4-service management** with per-card live status (version, PID, uptime, exit code, stdout/stderr).
- **One-click version check & upgrade** via a six-stage structured pipeline; per-stage debug logs and targeted failure hints (`EBADENGINE`, file lock, disk space, network).
- **Per-service proxy toggle**: each card has a "system proxy" switch; off-state actively clears proxy env vars to avoid session-injection pollution of child processes.
- **Switchable working directory**: top-bar button + dual-write persistence; running services need restart to pick up changes.
- **Node.js compatibility**: auto-detects npm in `Program Files`, `Program Files (x86)`, per-user, nvm symlink, and fnm default alias; checks Node version/arch against each package's `engines.node` at startup.
- **Portable across machines**: bundled `AI agent管理工作台.bat` wizard auto-detects Python / Node / fnm / workdir on first run; copy to a new Windows box and rerun the bat to reconfigure.
- **Structured logging**: dual-channel (UI panel level-filtered + on-disk full log) for easy post-mortem; DEBUG captures raw subprocess output and parameter details.
- **Optional tray icon & resource monitor**: graceful degradation when `pystray` / `Pillow` / `psutil` are missing.

### Requirements

- **OS**: Windows 10 / 11 (32-bit and 64-bit)
- **Python**: 3.10+
- **Node.js**: 22.0.0+ (required by `claude` and `pi`)
  - 32-bit Windows users: download the **Windows 32-bit Installer** from [nodejs.org](https://nodejs.org)
  - Recommended: [fnm](https://github.com/Schniz/fnm) or [nvm-windows](https://github.com/coreybutler/nvm-windows) for multi-version management
- **Disk**: ~200 MB (with optional deps)

### Install

1. **Clone**:
   ```bash
   git clone https://github.com/ROSSINHU/ai-agent-workbench.git
   cd ai-agent-workbench
   ```
2. **Install Python deps** (optional features; the core GUI works without them):
   ```bash
   pip install -r requirements.txt
   ```
3. **Install the AI services** you want to manage:
   ```bash
   npm install -g @anthropic-ai/claude-code@latest
   npm install -g @openai/codex@latest
   npm install -g @earendil-works/pi-coding-agent@latest
   ```
4. **First launch**: double-click `AI agent管理工作台.bat` and follow the wizard.

### Usage

- **Start a service**: click "启动" on its card.
- **Upgrade**: click "升级"; current vs latest version is shown in real time.
- **Toggle proxy**: use the "系统代理" switch on each card; takes effect immediately for check/upgrade; running services need restart.
- **Switch work dir**: top-bar "切换目录" button.
- **Logs**: switch levels in the UI panel (DEBUG / INFO / WARN / ERROR); full logs at `logs/workbench-YYYYMMDD.log`.

### Configuration

| File | Purpose | Priority |
|---|---|---|
| `workbench2.cfg` | bat wizard runtime config (Python / npm / fnm / workdir) | medium |
| `workbench_settings.json` | UI-managed workdir / proxy / hermes branch | high |
| `WORKBENCH_*` env vars | explicitly passed by bat | highest |

> ⚠ Both runtime configs contain personal paths and are listed in `.gitignore` — your local copy is never committed.
> See [`workbench2.cfg.example`](workbench2.cfg.example) / [`workbench_settings.json.example`](workbench_settings.json.example) for templates.

### Architecture

The codebase is split into one-way layers (`app → core → platform_windows`):

```
service_manager.py            # thin entry point (the bat still launches this)
workbench/
├── core.py                   # headless logic: config / processes / proxy / Node / logging / state
├── platform_windows.py       # Windows-specific code (registry system proxy)
└── app.py                    # Tkinter GUI (WorkbenchApp + main)
```

`core.py` has no Tkinter dependency and can be reused by a CLI, tests, or a future UI.

### Building a standalone exe

To ship without requiring Python on the target machine:

```bash
pip install pyinstaller psutil pystray Pillow
pyinstaller AIWorkbench.spec --noconfirm --clean
# output: dist/AIWorkbench.exe (~21 MB), double-click to run
```

You can copy `AIWorkbench.exe` anywhere on its own; `workbench2.cfg`,
`workbench_settings.json`, and `logs/` are created next to the exe.

### Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for branch strategy, commit conventions, and code style.

### License

[MIT](LICENSE).

### Acknowledgments

Inspired by the daily need to manage four AI agent CLIs side-by-side.
