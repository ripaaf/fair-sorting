@echo off
rem Fair Sorting - web mode. Other devices on your Wi-Fi can open the URL it prints (PIN protected).
setlocal
cd /d "%~dp0"
where python >nul 2>nul || (echo Python 3.10 or newer is required: https://www.python.org/downloads/ & pause & exit /b 1)
python -c "import PIL, webview, qrcode" >nul 2>nul || python -m pip install -r requirements.txt
python web_sorter.py %*
pause
