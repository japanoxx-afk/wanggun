@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ========================================
echo   Taejo Wanggeon Dummy Server
echo ========================================
"C:\Users\seo\AppData\Local\Programs\Python\Python314\python.exe" dummyserver.py
echo.
echo [server stopped]
pause
