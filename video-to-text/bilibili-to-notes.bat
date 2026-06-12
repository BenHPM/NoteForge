@echo off
chcp 65001 >nul
title NoteForge - B站视频转笔记
echo.
echo  ============================================================
echo    NoteForge - B站视频转笔记服务
echo    输入B站视频链接，自动下载音频并转写为笔记
echo  ============================================================
echo.

set PY=D:\ProgramData\TraeCN\NoteForge\video-to-text\envs\paraformer\python.exe
set SCRIPT=D:\ProgramData\TraeCN\NoteForge\video-to-text\scripts\paraformer_transcribe.py
set TEMP_DIR=D:\ProgramData\TraeCN\NoteForge\video-to-text\temp

echo  [INFO] 检查依赖工具...

yt-dlp --version >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo  [OK] 已安装 yt-dlp
) else (
    echo  [WARN] 未检测到 yt-dlp, 正在自动安装...
    echo.
    pip install yt-dlp
    if %ERRORLEVEL% NEQ 0 (
        echo  [ERROR] yt-dlp 安装失败
        echo  请手动运行: pip install yt-dlp
        pause
        exit /b 1
    )
    echo  [OK] yt-dlp 安装成功
)

echo.
echo  请粘贴B站视频链接:
echo  示例: https://www.bilibili.com/video/BV1YR5E6EE9o/
echo.
set /p VIDEO_URL="视频URL: "

echo.
echo  [INFO] 正在获取视频信息...
echo.

for /f "tokens=*" %%i in ('yt-dlp --get-title "%VIDEO_URL%" 2^>nul') do set VIDEO_TITLE=%%i

if not defined VIDEO_TITLE (
    echo  [WARN] 无法获取视频标题，将使用默认名称
    set VIDEO_NAME=bilibili_note
) else (
    echo  [OK] 视频标题: %VIDEO_TITLE%
    set VIDEO_NAME=%VIDEO_TITLE%
)

echo.
echo  ============================================================
echo  [Step 1/3] 从B站下载音频...
echo  ============================================================
echo.

mkdir "%TEMP_DIR%" 2>nul

yt-dlp --extract-audio --audio-format wav -o "%TEMP_DIR%\audio_temp.%%(ext)s" "%VIDEO_URL%"

if %ERRORLEVEL% NEQ 0 (
    echo  [ERROR] 音频下载失败
    echo  可能原因:
    echo    1. 视频链接无效或已失效
    echo    2. 需要登录权限
    echo    3. 网络连接问题
    pause
    exit /b 1
)

for %%F in ("%TEMP_DIR%\audio_temp.*") do set DOWNLOADED_FILE=%%F
echo  [OK] 音频下载完成: %DOWNLOADED_FILE%

echo.
echo  ============================================================
echo  [Step 2/3] 转换音频格式...
echo  ============================================================
echo.

set WAV_FILE=%TEMP_DIR%\audio_final.wav

if exist "%WAV_FILE%" del "%WAV_FILE%"

if exist "%DOWNLOADED_FILE%" (
    if "%DOWNLOADED_FILE%"=="%WAV_FILE%" (
        echo  [OK] 音频已是wav格式
    ) else (
        move "%DOWNLOADED_FILE%" "%WAV_FILE%"
        echo  [OK] 音频文件已重命名
    )
)

echo  [OK] 音频文件: %WAV_FILE%

echo.
echo  ============================================================
echo  [Step 3/3] 转写音频生成笔记...
echo  ============================================================
echo.

%PY% %SCRIPT% "%WAV_FILE%" "%VIDEO_NAME%"

echo.
if %ERRORLEVEL% EQU 0 (
    echo  ============================================================
    echo  ✅ 转写完成！笔记已保存
    echo  ============================================================
    echo.
    echo  [INFO] 清理临时文件...
    del "%WAV_FILE%" 2>nul
    echo  [OK] 临时音频文件已删除
) else (
    echo  ============================================================
    echo  ❌ 转写失败，请检查日志
    echo  ============================================================
)

echo.
pause
