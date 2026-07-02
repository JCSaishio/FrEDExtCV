@echo off
rem =====================================================================
rem  FrED Fiber Measure - double-click launcher (no console commands).
rem
rem  Works from inside the app folder AND as a copy placed anywhere
rem  (Desktop, another folder, ...): if fiber_measure.py is not next to
rem  this file, it falls back to the install location set below.
rem =====================================================================

set "APP_DIR=%~dp0"
if not exist "%APP_DIR%fiber_measure.py" (
    set "APP_DIR=C:\Users\saish\Desktop\FrED\FrEDExtCV\FrEDFiberMeasurewithStreamingv6\"
)
if not exist "%APP_DIR%fiber_measure.py" (
    echo Could not find fiber_measure.py.
    echo Edit the APP_DIR line inside this file so it points at the
    echo FrEDFiberMeasurewithStreamingv6 folder on this computer.
    pause
    exit /b 1
)

rem Prefer Anaconda's pythonw.exe so no console window stays open.
set "PYW=%USERPROFILE%\anaconda3\pythonw.exe"
if not exist "%PYW%" set "PYW=pythonw.exe"

start "FrED Fiber Measure" "%PYW%" "%APP_DIR%fiber_measure.py"
