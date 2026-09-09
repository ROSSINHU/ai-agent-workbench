@echo off
setlocal EnableExtensions EnableDelayedExpansion
rem ==================================================================
rem  Workbench 2.0 - portable launcher for service_manager.py
rem  Portable bootstrap, works on any Windows machine:
rem   [1] Work directory: configurable, persisted in workbench2.cfg,
rem       auto-loaded on every start
rem   [2] Node.js/npm: auto-detect (PATH + common install paths),
rem       manual input fallback; saved path takes priority next runs
rem   [3] fnm: smart detection; applies "fnm env" and uses the
rem       fnm-managed npm for pi when available
rem   [4] Probed paths are passed to the workbench via WORKBENCH_*
rem       environment variables (Windows style, backslash paths)
rem  Usage:
rem       run directly            - start with saved config
rem       argument "reset"        - force re-configuration
rem  Config file: workbench2.cfg (created next to this script)
rem  This script is pure ASCII on purpose: no codepage / locale issue.
rem ==================================================================
cd /d "%~dp0"
set "CFG=%~dp0workbench2.cfg"
set "PY_EXE="
set "NODE_NPM="
set "FNM_NPM="
set "FNM_EXE="
set "USE_FNM=0"
set "WORKDIR="
set "NEED_SETUP=0"

rem ---------------- load saved config ----------------
if exist "%CFG%" (
    echo [i] Loaded config: %CFG%
    for /f "usebackq tokens=1,* delims==" %%a in ("%CFG%") do (
        if /i "%%a"=="PY_EXE"   set "PY_EXE=%%b"
        if /i "%%a"=="NODE_NPM" set "NODE_NPM=%%b"
        if /i "%%a"=="FNM_NPM"  set "FNM_NPM=%%b"
        if /i "%%a"=="FNM_EXE"  set "FNM_EXE=%%b"
        if /i "%%a"=="USE_FNM"  set "USE_FNM=%%b"
        if /i "%%a"=="WORKDIR"  set "WORKDIR=%%b"
    )
) else (
    echo [i] No saved config found - first run setup.
    set "NEED_SETUP=1"
)
if /i "%~1"=="reset" set "NEED_SETUP=1"
if not defined PY_EXE   set "NEED_SETUP=1"
if not defined NODE_NPM set "NEED_SETUP=1"
if not defined WORKDIR  set "NEED_SETUP=1"

if "%NEED_SETUP%"=="0" (
    echo.
    echo  Current configuration:
    echo    Work dir : !WORKDIR!
    echo    npm      : !NODE_NPM!
    echo    fnm used : !USE_FNM!   fnm npm: !FNM_NPM!
    echo    Python   : !PY_EXE!
    echo.
    set /p "CHG=Reconfigure? [y/N]: "
    if /i not "!CHG!"=="y" goto launch
)

rem ---------------- [1] work directory ----------------
:get_workdir
echo.
if defined WORKDIR (
    set /p "WORKDIR=Work directory [Enter=keep: !WORKDIR!]: "
) else (
    set /p "WORKDIR=Work directory (e.g. D:\AIWorkbench): "
)
if not defined WORKDIR goto get_workdir
if exist "!WORKDIR!" goto workdir_ok
echo [!] Directory does not exist: !WORKDIR!
set /p "MK=Create it now? [Y/n]: "
if /i not "!MK!"=="n" mkdir "!WORKDIR!" 2>nul
if not exist "!WORKDIR!" (
    echo [!] Directory still missing - please input again.
    set "WORKDIR="
    goto get_workdir
)
:workdir_ok
echo [+] Work dir: !WORKDIR!

rem ---------------- [2] Node.js / npm ----------------
echo.
echo [i] Detecting Node.js / npm ...
if defined NODE_NPM if exist "!NODE_NPM!" (
    echo [+] Saved npm: !NODE_NPM!
    set /p "RD=Re-detect npm? [y/N]: "
    if /i "!RD!"=="y" set "NODE_NPM="
)
if not defined NODE_NPM call :detect_npm
if defined NODE_NPM (
    echo [+] Detected npm: !NODE_NPM!
    set /p "OKN=Use this npm? [Y/n]: "
    if /i "!OKN!"=="n" set "NODE_NPM="
)
:npm_input
if not defined NODE_NPM (
    echo [!] npm could not be auto-detected.
    set /p "NODE_NPM=Enter full path of npm.cmd (e.g. C:\Program Files\nodejs\npm.cmd): "
)
if not defined NODE_NPM goto npm_input
if not exist "!NODE_NPM!" (
    echo [!] Path does not exist: !NODE_NPM!
    set "NODE_NPM="
    goto npm_input
)
echo [+] npm: !NODE_NPM!

rem ---------------- [3] fnm smart detection ----------------
echo.
echo [i] Detecting fnm ...
call :detect_fnm
if defined FNM_EXE (
    echo [+] fnm found: !FNM_EXE!
    set /p "UF=Use fnm-managed Node for pi? [Y/n]: "
    if /i "!UF!"=="n" (
        set "USE_FNM=0"
        set "FNM_NPM="
    ) else (
        set "USE_FNM=1"
        if defined FNM_DEFAULT_NPM set "FNM_NPM=!FNM_DEFAULT_NPM!"
    )
) else (
    echo [i] fnm not found - pi will be resolved from PATH.
    set "USE_FNM=0"
    set "FNM_NPM="
)
if "!USE_FNM!"=="1" if not defined FNM_NPM (
    echo [!] fnm default-alias npm not found; pi upgrade may need manual FNM_NPM.
)

rem ---------------- [4] Python with tkinter ----------------
echo.
if defined PY_EXE if not "!PY_EXE!"=="py" if exist "!PY_EXE!" (
    echo [+] Saved Python: !PY_EXE!
    set /p "RP=Re-detect Python? [y/N]: "
    if /i "!RP!"=="y" set "PY_EXE="
)
if not defined PY_EXE (
    echo [i] Detecting Python with tkinter ...
    call :detect_py
)
:py_input
if not defined PY_EXE (
    echo [!] Python with tkinter could not be auto-detected.
    set /p "PY_EXE=Enter full path of pythonw.exe (must have tkinter): "
)
if not defined PY_EXE goto py_input
if /i not "!PY_EXE!"=="py" if not exist "!PY_EXE!" (
    echo [!] Path does not exist: !PY_EXE!
    set "PY_EXE="
    goto py_input
)
if /i not "!PY_EXE!"=="py" (
    "!PY_EXE!" -c "import tkinter" >nul 2>&1
    if errorlevel 1 (
        echo [!] This Python has no tkinter: !PY_EXE!
        set "PY_EXE="
        goto py_input
    )
)
echo [+] Python: !PY_EXE!

rem ---------------- save config ----------------
> "%CFG%" (
    echo PY_EXE=!PY_EXE!
    echo NODE_NPM=!NODE_NPM!
    echo FNM_NPM=!FNM_NPM!
    echo FNM_EXE=!FNM_EXE!
    echo USE_FNM=!USE_FNM!
    echo WORKDIR=!WORKDIR!
)
echo.
echo [i] Config saved: %CFG%

:launch
if not exist "!WORKDIR!" mkdir "!WORKDIR!" 2>nul
if "!USE_FNM!"=="1" (
    if not defined FNM_EXE call :detect_fnm
    if defined FNM_EXE (
        echo [i] Applying fnm environment ...
        for /f "usebackq delims=" %%i in (`"!FNM_EXE!" env --shell cmd`) do %%i
    )
)
set "WORKBENCH_DIR=!WORKDIR!"
set "WORKBENCH_SYSTEM_NPM=!NODE_NPM!"
set "WORKBENCH_FNM_NPM=!FNM_NPM!"
echo.
echo [i] Launching workbench (work dir: !WORKDIR!) ...
if /i "!PY_EXE!"=="py" (
    start "" py "%~dp0service_manager.py"
) else (
    start "" "!PY_EXE!" "%~dp0service_manager.py"
)
endlocal
exit /b 0

rem ================= subroutines =================

:detect_npm
rem Find npm.cmd: standard install locations first (highest signal,
rem immune to session-injected PATH), PATH lookup as fallback.
if exist "%ProgramFiles%\nodejs\npm.cmd" (
    set "NODE_NPM=%ProgramFiles%\nodejs\npm.cmd"
    goto :eof
)
if exist "%ProgramFiles(x86)%\nodejs\npm.cmd" (
    set "NODE_NPM=%ProgramFiles(x86)%\nodejs\npm.cmd"
    goto :eof
)
if exist "%LOCALAPPDATA%\Programs\nodejs\npm.cmd" (
    set "NODE_NPM=%LOCALAPPDATA%\Programs\nodejs\npm.cmd"
    goto :eof
)
if defined NVM_SYMLINK if exist "%NVM_SYMLINK%\npm.cmd" (
    set "NODE_NPM=%NVM_SYMLINK%\npm.cmd"
    goto :eof
)
where npm.cmd >nul 2>&1
if not errorlevel 1 (
    for /f "delims=" %%i in ('where npm.cmd') do (
        set "NODE_NPM=%%i"
        goto :eof
    )
)
if exist "%APPDATA%\npm\npm.cmd" (
    set "NODE_NPM=%APPDATA%\npm\npm.cmd"
    goto :eof
)
goto :eof

:detect_fnm
rem Find fnm executable and its default-alias npm.
set "FNM_EXE="
set "FNM_DEFAULT_NPM="
where fnm >nul 2>&1
if not errorlevel 1 (
    for /f "delims=" %%i in ('where fnm') do (
        set "FNM_EXE=%%i"
        goto fnm_done
    )
)
if exist "%APPDATA%\fnm\fnm.exe" set "FNM_EXE=%APPDATA%\fnm\fnm.exe"
if not defined FNM_EXE if exist "%LOCALAPPDATA%\fnm\fnm.exe" set "FNM_EXE=%LOCALAPPDATA%\fnm\fnm.exe"
if not defined FNM_EXE if exist "%USERPROFILE%\.cargo\bin\fnm.exe" set "FNM_EXE=%USERPROFILE%\.cargo\bin\fnm.exe"
:fnm_done
if defined FNM_EXE if exist "%APPDATA%\fnm\aliases\default\npm.cmd" set "FNM_DEFAULT_NPM=%APPDATA%\fnm\aliases\default\npm.cmd"
goto :eof

:detect_py
rem Find a Python that has tkinter: pythonw (no console window) preferred.
set "PY_EXE="
where pythonw.exe >nul 2>&1
if not errorlevel 1 (
    pythonw.exe -c "import tkinter" >nul 2>&1
    if not errorlevel 1 (
        for /f "delims=" %%i in ('where pythonw.exe') do (
            set "PY_EXE=%%i"
            goto :eof
        )
    )
)
py -c "import tkinter" >nul 2>&1
if not errorlevel 1 (
    set "PY_EXE=py"
    goto :eof
)
where python.exe >nul 2>&1
if not errorlevel 1 (
    python.exe -c "import tkinter" >nul 2>&1
    if not errorlevel 1 (
        for /f "delims=" %%i in ('where python.exe') do (
            set "PY_EXE=%%i"
            goto :eof
        )
    )
)
for %%D in ("%USERPROFILE%\miniconda3" "%USERPROFILE%\anaconda3" "%LOCALAPPDATA%\miniconda3") do (
    if exist "%%~D\pythonw.exe" (
        "%%~D\pythonw.exe" -c "import tkinter" >nul 2>&1
        if not errorlevel 1 (
            set "PY_EXE=%%~D\pythonw.exe"
            goto :eof
        )
    )
)
goto :eof
