@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>&1
if errorlevel 1 (
    echo Python 3.13 or later with Tk and Pillow is required.
    pause
    exit /b 1
)
py -c "import sys, tkinter, PIL; assert sys.version_info >= (3, 13)" >nul 2>&1
if errorlevel 1 (
    echo Python 3.13 or later with Tk and Pillow is required.
    echo Install Pillow with: py -m pip install Pillow
    pause
    exit /b 1
)
py MapMaker.pyw
if errorlevel 1 pause
