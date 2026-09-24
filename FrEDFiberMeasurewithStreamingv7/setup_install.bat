@echo off
REM Install the libraries for FrED Fiber Measure with Streaming (Windows).
REM Prefers the known Anaconda Python; falls back to whatever 'python' is on PATH
REM so it also works on other computers.
setlocal
set "ANACONDA=C:\Users\saish\anaconda3\python.exe"
if exist "%ANACONDA%" (
    "%ANACONDA%" "%~dp0setup_install.py"
) else (
    python "%~dp0setup_install.py"
)
echo.
pause
