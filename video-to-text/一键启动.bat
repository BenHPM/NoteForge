@echo off
chcp 65001 >nul
title NoteForge - 一键启动

echo.
echo  ============================================================
echo    NoteForge 智能转写服务 - 一键启动
echo  ============================================================
echo.

set PY=D:\ProgramData\TraeCN\NoteForge\video-to-text\envs\paraformer\python.exe
set WEB_SCRIPT=D:\ProgramData\TraeCN\NoteForge\video-to-text\web_server.py
set WORK_DIR=D:\ProgramData\TraeCN\NoteForge\video-to-text

cd /d %WORK_DIR%

echo  [1/3] 检查依赖...
%PY% -c "import flask" >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo  [INFO] 正在安装 Flask...
    %PY% -m pip install flask flask-cors -q
)

echo  [2/3] 启动服务...

start "" /B %PY% %WEB_SCRIPT% >nul 2>&1

timeout /t 3 /nobreak >nul

echo  [3/3] 打开浏览器...
start http://localhost:5000

echo.
echo  ✅ 服务已启动!
echo  📱 浏览器已自动打开
echo.
echo  💡 提示:
echo     - 关闭此窗口不会停止服务
echo     - 如需停止服务,请运行 "停止服务.bat"
echo     - 或直接关闭命令行窗口即可
echo.
echo  按任意键关闭此提示窗口...
pause >nul