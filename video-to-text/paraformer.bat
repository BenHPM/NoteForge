@echo off
chcp 65001 >nul
title Paraformer 视频转写工具 v2.0
echo.
echo  ============================================================
echo    Paraformer 视频转写工具 (默认引擎)
echo    已切换: Whisper -> Paraformer (快4-5倍!)
echo  ============================================================
echo.

set PY=D:\ProgramData\TraeCN\zmt-os\video-to-text\envs\paraformer\python.exe
set SCRIPT=D:\ProgramData\TraeCN\zmt-os\video-to-text\scripts\paraformer_transcribe.py

if "%~1"=="" (
    echo  用法:
    echo    %~nx0 ^<视频文件或目录^>
    echo.
    echo  示例:
    echo    %~nx0 video.mp4           - 转写单个视频
    echo    %~nx0 D:\videos\          - 批量转写目录
    echo.
    set /p INPUT="请输入视频路径: "
) else (
    set INPUT=%*
)

echo.
echo  [INFO] 使用引擎: Paraformer (FunASR)
echo  [INFO] 开始转写: %INPUT%
echo.

%PY% %SCRIPT% %INPUT%

echo.
if %ERRORLEVEL% EQU 0 (
    echo  ✅ 转写完成！
) else (
    echo  ❌ 转写失败，请检查文件路径
)

pause
