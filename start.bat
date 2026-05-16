@echo off
echo Stopping any running Pumpvision servers...

:: Kill any Python processes running Flask on port 5000
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5000" ^| findstr "LISTENING"') do (
    taskkill /F /PID %%a >nul 2>&1
)

:: Also kill any lingering flask/wsgi python processes
for /f "tokens=2" %%a in ('tasklist /FI "IMAGENAME eq python.exe" /FO list ^| findstr "PID"') do (
    taskkill /F /PID %%a >nul 2>&1
)
for /f "tokens=2" %%a in ('tasklist /FI "IMAGENAME eq python3.exe" /FO list ^| findstr "PID"') do (
    taskkill /F /PID %%a >nul 2>&1
)
for /f "tokens=2" %%a in ('tasklist /FI "IMAGENAME eq python3.13.exe" /FO list ^| findstr "PID"') do (
    taskkill /F /PID %%a >nul 2>&1
)

timeout /t 2 /nobreak >nul

echo Starting Pumpvision...
cd /d "%~dp0"
start "Pumpvision" "C:\Users\Rishab 2\AppData\Local\Python\bin\python.exe" -m flask --app wsgi:app run --host=0.0.0.0 --port=5000

timeout /t 3 /nobreak >nul
echo.
echo Pumpvision is running at http://192.168.1.9:5000
echo (Close the Pumpvision window to stop the server)
echo.
pause
