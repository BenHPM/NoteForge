@echo off
chcp 65001 >nul
title NoteForge v4.0 - 智能笔记锻造系统
echo.
echo  ============================================================
echo    NoteForge v4.0 - 智能笔记锻造系统
echo    ASR: Paraformer (FunASR) | LLM: Claude Sonnet (在线 API)
echo  ============================================================
echo.

set BASE=%~dp0
set PY=%BASE%envs\paraformer\python.exe
set TRANSCRIBE=%BASE%scripts\paraformer_transcribe.py
set ENGINE=%BASE%scripts\llm_note_engine.py

if not exist "%PY%" (
    echo  [ERROR] Python 隔离环境未找到: %PY%
    echo.
    echo  请先创建环境:
    echo    py -3.10 -m venv envs\paraformer
    echo    envs\paraformer\Scripts\activate
    echo    pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

echo  请选择操作:
echo.
echo    --- 转写 ---
echo    [1] 转写视频/音频文件（Paraformer ASR）
echo    [2] 批量转写（使用配置文件）
echo.
echo    --- 笔记生成 ---
echo    [3] 从转写文本生成笔记（LLM Engine）
echo    [4] 一键：转写 + 生成笔记（完整流程）
echo    [5] 批量生成所有笔记
echo.
echo    --- 高级功能 ---
echo    [6] YouTube 下载 + 转写 + 生成笔记
echo    [19] B站视频下载 + 转写 + 生成笔记（无需 Cookie）
echo    [20] 音频平台链接转笔记（小宇宙/喜马拉雅/荔枝FM 等）
echo    [7] 知识合成 - 单次（快速，适合 <=10 篇）
echo    [21] 知识合成 - 两阶段（推荐，含矛盾检测+域隔离）
echo    [22] 知识合成 - 增量更新（新增 1 篇同域笔记）
echo    [8] 仅质量检查
echo    [9] 会议音频 → 会议纪要
echo    [23] Token 使用统计
echo.
echo    --- Podcast RSS ---
echo    [10] 订阅 Podcast RSS
echo    [11] 查看已订阅 Podcasts
echo    [12] 同步 Podcast 新 Episodes
echo    [13] Podcast 新 Episodes 生成笔记
echo.
echo    --- 知识管理 ---
echo    [14] 搜索笔记
echo    [15] 笔记库概览（标签+统计）
echo.
echo    --- 飞书同步 ---
echo    [16] 同步笔记到飞书知识库（全部）
echo    [17] 同步指定文件到飞书
echo    [18] 预览同步计划（dry-run）
echo.
echo    [0] 退出
echo.
set /p MODE="请输入选项 (0-23): "

if "%MODE%"=="0" exit /b 0
if "%MODE%"=="1" goto :opt1
if "%MODE%"=="2" goto :opt2
if "%MODE%"=="3" goto :opt3
if "%MODE%"=="4" goto :opt4
if "%MODE%"=="5" goto :opt5
if "%MODE%"=="6" goto :opt6
if "%MODE%"=="7" goto :opt7
if "%MODE%"=="8" goto :opt8
if "%MODE%"=="9" goto :opt9
if "%MODE%"=="10" goto :opt10
if "%MODE%"=="11" goto :opt11
if "%MODE%"=="12" goto :opt12
if "%MODE%"=="13" goto :opt13
if "%MODE%"=="14" goto :opt14
if "%MODE%"=="15" goto :opt15
if "%MODE%"=="16" goto :opt16
if "%MODE%"=="17" goto :opt17
if "%MODE%"=="18" goto :opt18
if "%MODE%"=="19" goto :opt19
if "%MODE%"=="20" goto :opt20
if "%MODE%"=="21" goto :opt21
if "%MODE%"=="22" goto :opt22
if "%MODE%"=="23" goto :opt23

echo  [ERROR] 无效选项: %MODE%
pause
exit /b 1

:opt1
echo.
echo  请输入视频/音频文件路径:
set /p INPUT="文件路径: "
echo.
%PY% %TRANSCRIBE% "%INPUT%"
goto :done

:opt2
echo.
%PY% %TRANSCRIBE% all
goto :done

:opt3
echo.
echo  请输入转写文件路径或集数编号（如 ep01）:
set /p INPUT="输入: "
echo.
%PY% -X utf8 %ENGINE% --input %INPUT%
goto :done

:opt4
echo.
echo  请输入视频/音频文件路径:
set /p INPUT="文件路径: "
echo.
echo  [INFO] 自动转写 + 生成笔记...
%PY% -X utf8 %ENGINE% --input "%INPUT%"
goto :done

:opt5
echo.
echo  批量生成笔记（跳过已有）...
%PY% -X utf8 %ENGINE% --batch --skip-existing
goto :done

:opt6
echo.
echo  请输入 YouTube 视频或播放列表 URL:
set /p INPUT="URL: "
echo.
echo  [INFO] 检查 yt-dlp...
yt-dlp --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo  [ERROR] yt-dlp 未安装。请运行: pip install yt-dlp
    goto :done
)
echo  [INFO] 开始处理...
%PY% -X utf8 %ENGINE% --youtube "%INPUT%"
goto :done

:opt7
echo.
echo  知识合成模式（读取所有笔记生成跨集知识文档）...
echo  可选：输入指定集数（如 ep01 ep02）或留空处理全部:
set /p INPUT="输入（留空=全部）: "
if "%INPUT%"=="" (
    %PY% -X utf8 %ENGINE% --mode synthesis
) else (
    %PY% -X utf8 %ENGINE% --mode synthesis --input %INPUT%
)
goto :done

:opt8
echo.
echo  请输入笔记文件路径:
set /p INPUT="文件路径: "
echo.
%PY% -X utf8 %ENGINE% --check-only "%INPUT%"
goto :done

:opt9
echo.
echo  请输入会议音频文件路径（.mp3/.wav/.m4a）:
set /p INPUT="文件路径: "
echo.
echo  请输入会议主题（可选，留空自动提取）:
set /p TITLE="主题: "
if "%TITLE%"=="" (
    %PY% -X utf8 %ENGINE% --input "%INPUT%" --mode meeting
) else (
    %PY% -X utf8 %ENGINE% --input "%INPUT%" --mode meeting --title "%TITLE%"
)
goto :done

:opt10
echo.
echo  请输入 Podcast RSS URL 或主页 URL:
set /p INPUT="URL: "
echo.
%PY% -X utf8 %ENGINE% --podcast-subscribe "%INPUT%"
goto :done

:opt11
echo.
%PY% -X utf8 %ENGINE% --podcast-list
goto :done

:opt12
echo.
echo  请输入要同步的 Podcast 名称（slug）:
set /p INPUT="名称: "
echo.
%PY% -X utf8 %ENGINE% --podcast-sync "%INPUT%"
goto :done

:opt13
echo.
echo  请输入 Podcast 名称（或留空处理全部新 episodes）:
set /p INPUT="名称: "
if "%INPUT%"=="" (
    %PY% -X utf8 %ENGINE% --podcast-sync-all
) else (
    %PY% -X utf8 %ENGINE% --podcast-process "%INPUT%"
)
goto :done

:opt14
echo.
echo  请输入搜索关键词:
set /p INPUT="关键词: "
echo.
%PY% -X utf8 %ENGINE% --search "%INPUT%"
goto :done

:opt15
echo.
%PY% -X utf8 %ENGINE% --list-notes
goto :done

:opt16
echo.
echo  同步所有笔记到飞书知识库...
%PY% -X utf8 %BASE%..\scripts\feishu_sync.py --new-only
goto :done

:opt17
echo.
echo  请输入文件名关键词（如 第01集）:
set /p INPUT="关键词: "
echo.
%PY% -X utf8 %BASE%..\scripts\feishu_sync.py --file "%INPUT%"
goto :done

:opt18
echo.
echo  预览同步计划...
%PY% -X utf8 %BASE%..\scripts\feishu_sync.py --dry-run
goto :done

:opt19
echo.
echo  请输入 Bilibili 视频 URL 或 BV 号:
set /p INPUT="URL/BV号: "
echo.
echo  [INFO] 开始处理（双策略：yt-dlp → API 降级，无需 Cookie）...
%PY% -X utf8 %ENGINE% --bilibili "%INPUT%"
goto :done

:opt20
echo.
echo  请输入音频平台分享链接:
echo    支持: 小宇宙 / 喜马拉雅 / 荔枝FM 等
set /p INPUT="URL: "
echo.
echo  [INFO] 开始处理（yt-dlp 通用提取）...
%PY% -X utf8 %ENGINE% --audio-url "%INPUT%"
goto :done

:opt21
echo.
echo  两阶段知识合成（逐集提取 → 合并 + 矛盾检测）
echo  自动按知识域隔离，只合成同域笔记
echo.
%PY% -X utf8 %ENGINE% --mode synthesis-2stage
goto :done

:opt22
echo.
echo  增量更新知识合成文档
echo  请输入新增笔记的路径或集数编号:
set /p INPUT="输入: "
echo.
%PY% -X utf8 %ENGINE% --mode synthesis-incremental --input %INPUT%
goto :done

:opt23
echo.
echo  Token 使用统计:
echo.
if exist "%BASE%output\logs\token_usage_*.json" (
    type "%BASE%output\logs\token_usage_*.json" 2>nul | findstr "total_cost"
) else (
    echo  暂无 token 使用记录
)
echo.
goto :done

:done
echo.
if %ERRORLEVEL% EQU 0 (
    echo  ============================================================
    echo  操作完成
    echo  ============================================================
) else (
    echo  ============================================================
    echo  操作失败，请检查日志
    echo  ============================================================
)
echo.
pause
