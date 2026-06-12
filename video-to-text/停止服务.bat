@echo off
chcp 65001 >nul
title NoteForge - 停止服务

echo.
echo  ============================================================
echo    NoteForge - 停止服务
echo  ============================================================
echo.

echo  [INFO] 正在查找并停止 NoteForge 服务进程...

for /f "tokens=5" %%a in ('netstat -aon ^| findstr :5000 ^| findstr LISTENING') do (
    echo  [INFO] 找到进程 PID: %%a
    taskkill /PID %%a /F >nul 2>&1
    if %ERRORLEVEL% EQU 0 (
        echo  ✅ 进程已停止
    ) else (
        echo  ⚠️ 停止失败,可能服务未运行
    )
)

echo.
echo  💡 服务已停止,如需重新启动请运行 "一键启动.bat"
echo.
timeout /t 3