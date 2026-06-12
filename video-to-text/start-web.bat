@echo off
chcp 65001 >nul
title NoteForge Web 服务
echo.
echo  ============================================================
echo    NoteForge Web 服务启动器
echo    启动网页版B站视频转笔记服务
echo  ============================================================
echo.

set PY=D:\ProgramData\TraeCN\NoteForge\video-to-text\envs\paraformer\python.exe
set WEB_SCRIPT=D:\ProgramData\TraeCN\NoteForge\video-to-text\web_server.py

echo  [INFO] 检查依赖...

echo  [INFO] 检查 Flask...
%PY% -c "import flask" >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo  [WARN] 缺少 Flask,正在安装...
    %PY% -m pip install flask flask-cors
    if %ERRORLEVEL% NEQ 0 (
        echo  [ERROR] Flask 安装失败
        pause
        exit /b 1
    )
    echo  [OK] Flask 安装完成
) else (
    echo  [OK] Flask 已就绪
)

echo.
echo  [INFO] 启动Web服务...
echo  [INFO] 请在浏览器中访问: http://localhost:5000
echo  [INFO] 按 Ctrl+C 停止服务
echo.

cd /d D:\ProgramData\TraeCN\NoteForge\video-to-text
%PY% %WEB_SCRIPT%

echo.
pause
