# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 构建配置：AI Agent 工作台单文件免安装 exe。

构建（在含 pyinstaller 的环境中）：
    pyinstaller AIWorkbench.spec --noconfirm --clean

产物：dist/AIWorkbench.exe（双击即运行，无需安装 Python）。
说明：
- onefile：单 exe，首次运行解压到临时目录；
- windowed：不弹黑色控制台窗（服务进程本身用 CREATE_NEW_CONSOLE 独立开窗，不受影响）；
- 配置 workbench2.cfg / workbench_settings.json 与 logs 目录在运行时定位到
  exe 所在目录（见 workbench/core.py 的 _app_root()，sys.frozen 分支），
  因此便携用法是把 exe 单独拷到任意目录运行，配置就近生成在 exe 旁。
"""
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

hidden = []
# pystray 在 Windows 上动态加载后端，需显式收集其子模块，避免托盘功能丢失
hidden += collect_submodules("pystray")
# psutil / Pillow 通常有内置 hook，这里补收集确保 onefile 下可用
hidden += collect_submodules("psutil")
hidden += ["PIL.Image", "PIL.ImageDraw", "PIL.ImageFont"]

datas = []
datas += collect_data_files("pystray")

a = Analysis(
    ["service_manager.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "workbench",
        "workbench.core",
        "workbench.app",
        "workbench.platform_windows",
    ] + hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 排除明显不需要的大模块，减小体积
    excludes=["numpy", "pandas", "matplotlib", "pytest", "PySide2", "PyQt5"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="AIWorkbench",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # --windowed：GUI 程序，无控制台
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
