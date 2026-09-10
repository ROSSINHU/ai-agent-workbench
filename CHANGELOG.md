# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- PyInstaller build spec (`AIWorkbench.spec`) producing a portable, single-file, no-install `dist/AIWorkbench.exe` (~21 MB). Runtime config (`workbench2.cfg`, `workbench_settings.json`) and `logs/` resolve next to the exe, so it can be copied to any folder and run standalone.
- Standalone-exe download/build instructions in the README.

### Changed
- Refactored the ~2,500-line `service_manager.py` monolith into a layered `workbench/` package with **zero behavior change**:
  - `workbench/core.py` — headless logic: config persistence / process control / proxy / Node detection / logging / state (no Tkinter dependency).
  - `workbench/platform_windows.py` — Windows-specific registry system-proxy reader.
  - `workbench/app.py` — the Tkinter GUI (`WorkbenchApp` + `main`).
  - `service_manager.py` is now a thin entry point (`from workbench.app import main`); the launcher `.bat` is unchanged.
- Introduced an `APP_ROOT` anchor so config/log paths resolve correctly both when running from source and when frozen by PyInstaller.

### Fixed
- Post-upgrade version check no longer reports the old version after a successful `npm install -g ...@latest`. On Windows, npm can exit 0 while a large native binary (e.g. codex's ~300 MB `codex.exe`) is still being moved/scanned by Defender, so the immediate `--version` read occasionally hit the stale binary. Stage 4 now retries with capped linear backoff until the version advances (npm packages) or becomes readable (git-based hermes), and warns clearly instead of silently showing the old version.

### Internal
- CI now byte-compiles and AST-parses the entire `workbench/` package, not only the thin entry point.

## [1.0.0] - 2026-09-09

### Added
- Initial open-source release.
- Unified management for four local AI agent services: `claude` (Anthropic Claude Code), `hermes` (NousResearch hermes-agent), `codex` (OpenAI codex-cli), `pi` (earendil pi-coding-agent).
- Start / stop / restart with real-time status (PID, uptime, exit code, live stdout/stderr).
- One-click version check and one-click upgrade for each service.
- Six-stage structured upgrade pipeline with per-stage debug logs and targeted failure hints (`EBADENGINE`, file lock, disk space, network).
- Per-service system proxy toggle (persisted; cleared when off to avoid session-injection pollution of child processes).
- Configurable working directory with top-bar switch button and dual-write persistence (settings + cfg).
- Portable launcher (`AI agent管理工作台.bat`) that auto-detects:
  - work directory
  - Node.js / npm install path (supports 64-bit `Program Files`, 32-bit `Program Files (x86)`, per-user `LOCALAPPDATA\Programs\nodejs`, and nvm symlink)
  - fnm install and default-alias npm
  - Python interpreter with tkinter
- Node environment detection (version + arch `x64` / `ia32` / `arm64`) with `engines.node` pre-check and `EBADENGINE` upgrade-failure hint.
- Dual-channel logging: UI panel (level-filtered) + on-disk log file (full DEBUG for post-mortem).
- Optional system tray icon (requires `pystray` + `Pillow`).
- Optional system resource monitor panel (requires `psutil`).
- GitHub Actions CI: Python 3.10 / 3.11 / 3.12 compile check on `windows-latest`.
