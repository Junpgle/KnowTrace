@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 "%~dp0start.py" %*
) else (
    python "%~dp0start.py" %*
)
if errorlevel 1 (
    echo.
    echo KnowTrace 启动失败，请检查上面的提示。
    pause
)
endlocal
