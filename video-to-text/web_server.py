"""
NoteForge Web 服务
提供视频/音频转笔记的Web API接口（接入 LLMNoteEngine 完整流水线）
"""
import os
import sys
import json
import time
import shutil
import subprocess
import threading
import uuid
from pathlib import Path
from datetime import datetime
from flask import Flask, request, jsonify, render_template_string
from flask_cors import CORS
from werkzeug.utils import secure_filename

# 接入 LLMNoteEngine
SCRIPT_DIR = Path(__file__).parent / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from llm_note_engine import LLMNoteEngine

app = Flask(__name__)
CORS(app)

BASE_DIR = Path(__file__).parent
TEMP_DIR = BASE_DIR / "temp"
OUTPUT_DIR = BASE_DIR / "output" / "notes"
PYTHON_EXE = BASE_DIR / "envs" / "paraformer" / "python.exe"
TRANSCRIBE_SCRIPT = BASE_DIR / "scripts" / "paraformer_transcribe.py"

TEMP_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_VIDEO_EXTENSIONS = {'.mp4', '.avi', '.mkv', '.mov', '.flv', '.wmv', '.m4v', '.webm'}
ALLOWED_AUDIO_EXTENSIONS = {'.wav', '.mp3', '.m4a', '.flac', '.aac', '.ogg'}
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2GB

app.config['MAX_CONTENT_LENGTH'] = MAX_FILE_SIZE

# 初始化 LLMNoteEngine（全局单例）
engine = LLMNoteEngine(config_path=str(BASE_DIR / "config" / "llm_engine_config.yaml"))


def get_video_title(video_url: str) -> str:
    """
    使用yt-dlp获取视频标题
    """
    try:
        result = subprocess.run(
            ["yt-dlp", "--get-title", video_url],
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def download_audio(video_url: str, output_path: str) -> dict:
    """
    下载音频（多平台支持：B站双策略降级 + yt-dlp 通用提取）
    Returns: {"success": bool, "path": str, "title": str, "method": str}
    """
    # B站链接：使用 bilibili_download 双策略（yt-dlp → API 降级）
    if 'bilibili.com' in video_url or 'b23.tv' in video_url:
        try:
            from bilibili_download import download_bilibili
            result = download_bilibili(video_url, output_path)
            if result.get('success'):
                return result
        except Exception:
            pass

    # 通用平台：yt-dlp
    try:
        cmd = [
            "yt-dlp", "--no-update",
            "--extract-audio", "--audio-format", "mp3",
            "--no-playlist", "-o", output_path, video_url
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode == 0 and os.path.exists(output_path):
            return {"success": True, "path": output_path, "title": "", "method": "yt-dlp"}
    except Exception:
        pass

    return {"success": False, "error": "所有下载策略均失败"}


def generate_notes_with_engine(audio_path: str, title: str) -> tuple:
    """
    使用 LLMNoteEngine 生成笔记（完整流水线：转录 + LLM + 质量门禁）

    Returns:
        (transcript_text, note_markdown) 元组
    """
    try:
        result = engine.generate_note(audio_path, title=title)

        # 读取转录文本
        transcript_text = ""
        transcript_path = engine.transcripts_dir / f"{Path(audio_path).stem}.txt"
        if transcript_path.exists():
            with open(transcript_path, 'r', encoding='utf-8') as f:
                transcript_text = f.read()

        # 读取生成的笔记
        note_md = ""
        if result and not result.error:
            note_path = engine.notes_dir / f"{title or Path(audio_path).stem}.md"
            if note_path.exists():
                with open(note_path, 'r', encoding='utf-8') as f:
                    note_md = f.read()
            elif result.content:
                note_md = result.content

        return transcript_text, note_md
    except Exception as e:
        print(f"[Web] LLM 引擎错误: {e}")
        return None, None

def extract_audio_from_video(video_path: str, audio_path: str) -> bool:
    """
    使用ffmpeg从视频中提取音频
    """
    try:
        cmd = [
            'ffmpeg', '-y', '-i', video_path,
            '-vn',
            '-acodec', 'pcm_s16le',
            '-ar', '16000',
            '-ac', '1',
            audio_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        return result.returncode == 0 and os.path.exists(audio_path)
    except Exception:
        return False


def process_local_file(task_id: str, file_path: str, file_name: str):
    """
    处理本地上传的文件(视频或音频)
    """
    tasks[task_id] = {
        "status": "processing",
        "progress": 0,
        "message": f"正在处理: {file_name}",
        "result": None,
        "error": None
    }
    
    try:
        file_ext = Path(file_path).suffix.lower()
        output_name = Path(file_name).stem
        
        # 如果是视频文件,需要提取音频
        if file_ext in ALLOWED_VIDEO_EXTENSIONS:
            tasks[task_id]["message"] = "正在提取音频..."
            tasks[task_id]["progress"] = 20
            
            audio_path = str(TEMP_DIR / f"{task_id}.wav")
            if not extract_audio_from_video(file_path, audio_path):
                tasks[task_id]["status"] = "error"
                tasks[task_id]["error"] = "音频提取失败"
                if os.path.exists(file_path):
                    os.remove(file_path)
                return
            
            # 删除上传的视频文件
            if os.path.exists(file_path):
                os.remove(file_path)
        
        # 如果是音频文件,直接使用
        elif file_ext in ALLOWED_AUDIO_EXTENSIONS:
            audio_path = file_path
        else:
            tasks[task_id]["status"] = "error"
            tasks[task_id]["error"] = f"不支持的文件格式: {file_ext}"
            if os.path.exists(file_path):
                os.remove(file_path)
            return
        
        tasks[task_id]["message"] = "正在转写音频并生成 LLM 笔记（可能需要几分钟）..."
        tasks[task_id]["progress"] = 60

        # 使用 LLMNoteEngine 完整流水线
        raw_text, note_md = generate_notes_with_engine(audio_path, output_name)

        # 清理临时文件
        if os.path.exists(audio_path) and audio_path != file_path:
            os.remove(audio_path)

        if note_md:
            tasks[task_id]["status"] = "completed"
            tasks[task_id]["progress"] = 100
            tasks[task_id]["message"] = "笔记生成完成"
            tasks[task_id]["result"] = {
                "title": output_name,
                "raw_text": raw_text or "",
                "smart_notes": note_md,
                "word_count": len(note_md.replace('\n', '').replace(' ', ''))
            }
        else:
            tasks[task_id]["status"] = "error"
            tasks[task_id]["error"] = "LLM 笔记生成失败"
    
    except Exception as e:
        tasks[task_id]["status"] = "error"
        tasks[task_id]["error"] = str(e)
        # 清理上传的文件
        if os.path.exists(file_path):
            os.remove(file_path)


# 存储任务状态
tasks = {}


def process_task(task_id: str, video_url: str):
    """
    后台处理任务
    """
    tasks[task_id] = {
        "status": "processing",
        "progress": 0,
        "message": "正在获取视频信息...",
        "result": None,
        "error": None
    }
    
    try:
        tasks[task_id]["message"] = "正在下载音频..."
        tasks[task_id]["progress"] = 20

        # 下载音频（多平台支持）
        audio_path = str(TEMP_DIR / f"{task_id}.mp3")
        dl_result = download_audio(video_url, audio_path)
        if not dl_result.get('success'):
            tasks[task_id]["status"] = "error"
            tasks[task_id]["error"] = dl_result.get('error', '音频下载失败')
            return

        title = dl_result.get('title', '') or get_video_title(video_url) or ''
        audio_path = dl_result.get('path', audio_path)

        tasks[task_id]["message"] = "正在转写音频并生成 LLM 笔记（可能需要几分钟）..."
        tasks[task_id]["progress"] = 60

        # 使用 LLMNoteEngine 完整流水线
        raw_text, note_md = generate_notes_with_engine(audio_path, title)

        # 清理临时文件
        if os.path.exists(audio_path):
            os.remove(audio_path)

        if note_md:
            tasks[task_id]["status"] = "completed"
            tasks[task_id]["progress"] = 100
            tasks[task_id]["message"] = "笔记生成完成"
            tasks[task_id]["result"] = {
                "title": title,
                "raw_text": raw_text or "",
                "smart_notes": note_md,
                "word_count": len(note_md.replace('\n', '').replace(' ', ''))
            }
        else:
            tasks[task_id]["status"] = "error"
            tasks[task_id]["error"] = "LLM 笔记生成失败"

    except Exception as e:
        tasks[task_id]["status"] = "error"
        tasks[task_id]["error"] = str(e)


@app.route('/')
def index():
    """
    主页 - 返回响应式Web界面
    """
    return render_template_string(HTML_TEMPLATE)


@app.route('/api/transcribe', methods=['POST'])
def api_transcribe():
    """
    API: 开始转写任务(支持URL和本地文件)
    """
    # 检查是否是文件上传
    if 'file' in request.files:
        file = request.files['file']
        if file.filename == '':
            return jsonify({"success": False, "error": "未选择文件"}), 400
        
        file_ext = Path(file.filename).suffix.lower()
        if file_ext not in ALLOWED_VIDEO_EXTENSIONS and file_ext not in ALLOWED_AUDIO_EXTENSIONS:
            return jsonify({
                "success": False,
                "error": f"不支持的文件格式: {file_ext}"
            }), 400
        
        task_id = str(uuid.uuid4())
        upload_path = str(TEMP_DIR / f"{task_id}_{secure_filename(file.filename)}")
        file.save(upload_path)
        
        # 启动后台线程处理
        thread = threading.Thread(
            target=process_local_file,
            args=(task_id, upload_path, file.filename)
        )
        thread.start()
        
        return jsonify({
            "success": True,
            "task_id": task_id,
            "message": "文件上传成功,开始处理"
        })
    
    # 处理URL转写
    data = request.get_json()
    video_url = data.get('url', '').strip()
    
    if not video_url:
        return jsonify({"success": False, "error": "请输入视频链接"}), 400
    
    if not video_url.startswith(('http://', 'https://')):
        return jsonify({"success": False, "error": "请输入有效的 URL"}), 400
    
    task_id = str(uuid.uuid4())
    
    # 启动后台线程处理
    thread = threading.Thread(target=process_task, args=(task_id, video_url))
    thread.start()
    
    return jsonify({
        "success": True,
        "task_id": task_id,
        "message": "任务已创建"
    })


@app.route('/api/status/<task_id>', methods=['GET'])
def api_status(task_id):
    """
    API: 查询任务状态
    """
    task = tasks.get(task_id)
    if not task:
        return jsonify({"success": False, "error": "任务不存在"}), 404
    
    return jsonify({
        "success": True,
        "data": task
    })


@app.route('/api/tasks', methods=['GET'])
def api_tasks():
    """
    API: 获取所有任务列表
    """
    return jsonify({
        "success": True,
        "data": tasks
    })


HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>NoteForge - 视频/音频转笔记</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 20px;
        }
        
        .container {
            background: white;
            border-radius: 20px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            width: 100%;
            max-width: 600px;
            padding: 40px;
            animation: fadeInUp 0.6s ease;
        }
        
        @keyframes fadeInUp {
            from {
                opacity: 0;
                transform: translateY(30px);
            }
            to {
                opacity: 1;
                transform: translateY(0);
            }
        }
        
        .header {
            text-align: center;
            margin-bottom: 30px;
        }
        
        .header h1 {
            font-size: 28px;
            color: #333;
            margin-bottom: 10px;
        }
        
        .header p {
            color: #666;
            font-size: 14px;
        }
        
        .input-group {
            margin-bottom: 20px;
        }
        
        .input-group label {
            display: block;
            margin-bottom: 8px;
            color: #333;
            font-weight: 500;
        }
        
        .input-group input {
            width: 100%;
            padding: 14px 18px;
            border: 2px solid #e0e0e0;
            border-radius: 12px;
            font-size: 16px;
            transition: all 0.3s ease;
            outline: none;
        }
        
        .input-group input:focus {
            border-color: #667eea;
            box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
        }
        
        .mode-tabs {
            display: flex;
            gap: 10px;
            margin-bottom: 20px;
        }
        
        .tab-btn {
            flex: 1;
            padding: 12px;
            border: 2px solid #e0e0e0;
            background: white;
            color: #666;
            border-radius: 12px;
            font-size: 15px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.3s ease;
        }
        
        .tab-btn.active {
            border-color: #667eea;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
        }
        
        #urlInput {
            display: none;
        }
        
        #urlInput.active {
            display: block;
        }
        
        .upload-area {
            display: none;
            margin-bottom: 20px;
            padding: 40px 20px;
            border: 3px dashed #ccc;
            border-radius: 12px;
            text-align: center;
            cursor: pointer;
            transition: all 0.3s ease;
            background: #fafafa;
        }
        
        .upload-area.dragover {
            border-color: #667eea;
            background: #f0f4ff;
        }
        
        .upload-area.active {
            display: block;
        }
        
        .upload-icon {
            font-size: 48px;
            margin-bottom: 10px;
        }
        
        .upload-text {
            color: #999;
            font-size: 14px;
        }
        
        .upload-text strong {
            color: #667eea;
        }
        
        .supported-formats {
            margin-top: 10px;
            font-size: 12px;
            color: #bbb;
        }
        
        .file-preview {
            display: none;
            margin-bottom: 20px;
            padding: 15px;
            background: #f0f9ff;
            border-radius: 12px;
            border-left: 4px solid #667eea;
        }
        
        .file-preview.active {
            display: flex;
            align-items: center;
            gap: 12px;
        }
        
        .file-info {
            flex: 1;
        }
        
        .file-name {
            font-weight: 600;
            color: #333;
            word-break: break-all;
        }
        
        .file-size {
            font-size: 12px;
            color: #999;
            margin-top: 4px;
        }
        
        .file-remove {
            width: 32px;
            height: 32px;
            border-radius: 50%;
            background: #fee;
            color: #e53e3e;
            border: none;
            cursor: pointer;
            font-size: 18px;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        
        .btn {
            width: 100%;
            padding: 16px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            border-radius: 12px;
            font-size: 18px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.3s ease;
            margin-top: 10px;
        }
        
        .btn:hover {
            transform: translateY(-2px);
            box-shadow: 0 8px 25px rgba(102, 126, 234, 0.4);
        }
        
        .btn:disabled {
            opacity: 0.6;
            cursor: not-allowed;
            transform: none;
        }
        
        .progress-container {
            display: none;
            margin-top: 20px;
            padding: 20px;
            background: #f8f9fa;
            border-radius: 12px;
        }
        
        .progress-container.active {
            display: block;
        }
        
        .progress-bar {
            width: 100%;
            height: 8px;
            background: #e0e0e0;
            border-radius: 4px;
            overflow: hidden;
            margin-bottom: 12px;
        }
        
        .progress-bar-fill {
            height: 100%;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            border-radius: 4px;
            transition: width 0.5s ease;
            width: 0%;
        }
        
        .progress-text {
            text-align: center;
            color: #666;
            font-size: 14px;
        }
        
        .result-container {
            display: none;
            margin-top: 20px;
            padding: 20px;
            background: #f0f9ff;
            border-radius: 12px;
            border-left: 4px solid #667eea;
        }
        
        .result-container.active {
            display: block;
        }
        
        .result-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 15px;
        }
        
        .result-title {
            font-size: 18px;
            font-weight: 600;
            color: #333;
        }
        
        .result-tabs {
            display: flex;
            gap: 8px;
        }
        
        .result-tab-btn {
            padding: 6px 14px;
            border: 1px solid #ddd;
            background: white;
            border-radius: 6px;
            font-size: 13px;
            cursor: pointer;
            transition: all 0.2s ease;
        }
        
        .result-tab-btn.active {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border-color: transparent;
        }
        
        .result-text {
            color: #666;
            line-height: 1.8;
            max-height: 450px;
            overflow-y: auto;
            white-space: pre-wrap;
        }
        
        .result-text.markdown {
            white-space: normal;
        }
        
        .result-text.markdown h2 {
            font-size: 16px;
            color: #333;
            margin: 15px 0 8px 0;
            padding-bottom: 5px;
            border-bottom: 2px solid #667eea;
        }
        
        .result-text.markdown h1 {
            font-size: 20px;
            color: #667eea;
            margin-bottom: 10px;
        }
        
        .result-text.markdown blockquote {
            background: #e8f4fd;
            padding: 10px 15px;
            border-radius: 8px;
            margin: 10px 0;
            font-size: 13px;
            color: #555;
        }
        
        .result-text.markdown ol {
            padding-left: 20px;
        }
        
        .result-text.markdown li {
            margin: 8px 0;
            line-height: 1.6;
        }
        
        .result-text.markdown code {
            background: #f0f0f0;
            padding: 2px 6px;
            border-radius: 4px;
            font-size: 13px;
        }
        
        .result-text.markdown hr {
            border: none;
            border-top: 1px solid #e0e0e0;
            margin: 15px 0;
        }
        
        .error-container {
            display: none;
            margin-top: 20px;
            padding: 20px;
            background: #fef2f2;
            border-radius: 12px;
            border-left: 4px solid #ef4444;
        }
        
        .error-container.active {
            display: block;
        }
        
        .error-text {
            color: #dc2626;
        }
        
        .features {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 15px;
            margin-top: 30px;
            padding-top: 20px;
            border-top: 1px solid #e0e0e0;
        }
        
        .feature {
            text-align: center;
            padding: 15px;
        }
        
        .feature-icon {
            font-size: 30px;
            margin-bottom: 8px;
        }
        
        .feature-title {
            font-size: 14px;
            color: #333;
            font-weight: 600;
        }
        
        .feature-desc {
            font-size: 12px;
            color: #999;
            margin-top: 4px;
        }
        
        @media (max-width: 480px) {
            .container {
                padding: 25px;
                margin: 10px;
            }
            
            .header h1 {
                font-size: 22px;
            }
            
            .input-group input {
                padding: 12px 14px;
                font-size: 14px;
            }
            
            .mode-tabs {
                flex-direction: column;
                gap: 8px;
            }
            
            .tab-btn {
                font-size: 14px;
                padding: 10px;
            }
            
            .upload-area {
                padding: 30px 15px;
            }
            
            .upload-icon {
                font-size: 36px;
            }
            
            .btn {
                padding: 14px;
                font-size: 16px;
            }
            
            .features {
                grid-template-columns: repeat(2, 1fr);
                gap: 10px;
            }
            
            .feature {
                padding: 10px;
            }
            
            .file-preview.active {
                flex-direction: column;
                align-items: flex-start;
            }
            
            .file-remove {
                align-self: flex-end;
            }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📝 NoteForge</h1>
            <p>智能语音转笔记 - 多平台视频/音频/本地文件</p>
        </div>
        
        <div class="mode-tabs">
            <button class="tab-btn active" onclick="switchTab('url')">🌐 视频/音频链接</button>
            <button class="tab-btn" onclick="switchTab('upload')">📁 上传本地文件</button>
        </div>
        
        <div id="urlInput" class="active">
            <div class="input-group">
                <label for="videoUrl">视频/音频链接</label>
                <input type="text" id="videoUrl" placeholder="B站/YouTube/小宇宙/喜马拉雅/抖音 等链接..." 
                       value="https://www.bilibili.com/video/BV1YR5E6EE9o/">
            </div>
        </div>
        
        <div id="uploadArea" class="upload-area" onclick="document.getElementById('fileInput').click();">
            <div class="upload-icon">☁️</div>
            <div class="upload-text">
                拖放视频/音频文件到此处<br/>
                <strong>或点击选择文件</strong>
            </div>
            <div class="supported-formats">支持格式：MP4, MKV, AVI, MOV, WAV, MP3, M4A 等</div>
            <input type="file" id="fileInput" accept="video/*,audio/*" style="display: none;">
        </div>
        
        <div id="filePreview" class="file-preview">
            <div class="file-info">
                <div class="file-name" id="fileName"></div>
                <div class="file-size" id="fileSize"></div>
            </div>
            <button class="file-remove" onclick="removeFile()">×</button>
        </div>
        
        <button class="btn" id="startBtn" onclick="startTranscribe()">
            开始转写
        </button>
        
        <div class="progress-container" id="progressContainer">
            <div class="progress-bar">
                <div class="progress-bar-fill" id="progressBar"></div>
            </div>
            <div class="progress-text" id="progressText">准备中...</div>
        </div>
        
        <div class="result-container" id="resultContainer">
            <div class="result-header">
                <div class="result-title" id="resultTitle"></div>
                <div class="result-tabs">
                    <button class="result-tab-btn active" onclick="switchResultTab('notes', this)">📝 智能笔记</button>
                    <button class="result-tab-btn" onclick="switchResultTab('raw', this)">📄 原文</button>
                </div>
            </div>
            <div class="result-text markdown" id="resultTextNotes"></div>
            <div class="result-text" id="resultTextRaw" style="display: none;"></div>
        </div>
        
        <div class="error-container" id="errorContainer">
            <div class="error-text" id="errorText"></div>
        </div>
        
        <div class="features">
            <div class="feature">
                <div class="feature-icon">🎬</div>
                <div class="feature-title">在线下载</div>
                <div class="feature-desc">自动提取B站音频</div>
            </div>
            <div class="feature">
                <div class="feature-icon">📁</div>
                <div class="feature-title">本地上传</div>
                <div class="feature-desc">拖拽或选择文件</div>
            </div>
            <div class="feature">
                <div class="feature-icon">🎯</div>
                <div class="feature-title">语音识别</div>
                <div class="feature-desc">Paraformer引擎</div>
            </div>
            <div class="feature">
                <div class="feature-icon">⚡</div>
                <div class="feature-title">快速高效</div>
                <div class="feature-desc">准确率96%+</div>
            </div>
        </div>
    </div>
    
    <script>
        let currentTaskId = null;
        let statusInterval = null;
        let selectedFile = null;
        
        function switchTab(mode) {
            document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
            event.target.classList.add('active');
            
            if (mode === 'url') {
                document.getElementById('urlInput').classList.add('active');
                document.getElementById('uploadArea').classList.remove('active');
            } else {
                document.getElementById('urlInput').classList.remove('active');
                document.getElementById('uploadArea').classList.add('active');
            }
        }
        
        // Drag and drop handlers
        const uploadArea = document.getElementById('uploadArea');
        const fileInput = document.getElementById('fileInput');
        
        uploadArea.addEventListener('dragover', (e) => {
            e.preventDefault();
            uploadArea.classList.add('dragover');
        });
        
        uploadArea.addEventListener('dragleave', () => {
            uploadArea.classList.remove('dragover');
        });
        
        uploadArea.addEventListener('drop', (e) => {
            e.preventDefault();
            uploadArea.classList.remove('dragover');
            
            const files = e.dataTransfer.files;
            if (files.length > 0) {
                handleFileSelect(files[0]);
            }
        });
        
        fileInput.addEventListener('change', (e) => {
            if (e.target.files.length > 0) {
                handleFileSelect(e.target.files[0]);
            }
        });
        
        function handleFileSelect(file) {
            const validTypes = [
                'video/mp4', 'video/x-msvideo', 'video/x-matroska', 'video/quicktime',
                'audio/wav', 'audio/mpeg', 'audio/mp4', 'audio/flac', 'audio/aac'
            ];
            const validExtensions = ['.mp4', '.avi', '.mkv', '.mov', '.wav', '.mp3', '.m4a', '.flac'];
            const ext = '.' + file.name.split('.').pop().toLowerCase();
            
            if (!validExtensions.includes(ext)) {
                showError('不支持的文件格式: ' + ext);
                return;
            }
            
            selectedFile = file;
            document.getElementById('fileName').textContent = file.name;
            document.getElementById('fileSize').textContent = formatFileSize(file.size);
            document.getElementById('filePreview').classList.add('active');
        }
        
        function removeFile() {
            selectedFile = null;
            document.getElementById('fileInput').value = '';
            document.getElementById('filePreview').classList.remove('active');
        }
        
        function formatFileSize(bytes) {
            if (bytes === 0) return '0 Bytes';
            const k = 1024;
            const sizes = ['Bytes', 'KB', 'MB', 'GB'];
            const i = Math.floor(Math.log(bytes) / Math.log(k));
            return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
        }
        
        async function startTranscribe() {
            // 检查是否有活动文件上传模式
            const uploadActive = document.getElementById('uploadArea').classList.contains('active');
            
            if (uploadActive) {
                if (!selectedFile) {
                    showError('请先选择要上传的文件');
                    return;
                }
            } else {
                const videoUrl = document.getElementById('videoUrl').value.trim();
                
                if (!videoUrl) {
                    showError('请输入B站视频链接');
                    return;
                }
            }
            
            // 重置UI
            document.getElementById('resultContainer').classList.remove('active');
            document.getElementById('errorContainer').classList.remove('active');
            document.getElementById('progressContainer').classList.add('active');
            document.getElementById('startBtn').disabled = true;
            document.getElementById('startBtn').textContent = '处理中...';
            
            try {
                let response;
                
                if (uploadActive && selectedFile) {
                    // 上传本地文件
                    const formData = new FormData();
                    formData.append('file', selectedFile);
                    
                    response = await fetch('/api/transcribe', {
                        method: 'POST',
                        body: formData
                    });
                } else {
                    // URL转写
                    const videoUrl = document.getElementById('videoUrl').value.trim();
                    response = await fetch('/api/transcribe', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json'
                        },
                        body: JSON.stringify({ url: videoUrl })
                    });
                }
                
                const data = await response.json();
                
                if (data.success) {
                    currentTaskId = data.task_id;
                    startStatusPolling(data.task_id);
                } else {
                    showError(data.error || '创建任务失败');
                    resetUI();
                }
            } catch (error) {
                showError('网络请求失败: ' + error.message);
                resetUI();
            }
        }
        
        function startStatusPolling(taskId) {
            statusInterval = setInterval(async () => {
                try {
                    const response = await fetch(`/api/status/${taskId}`);
                    const data = await response.json();
                    
                    if (data.success) {
                        const task = data.data;
                        updateProgress(task.progress, task.message);
                        
                        if (task.status === 'completed') {
                            clearInterval(statusInterval);
                            showResult(task.result);
                            resetUI();
                        } else if (task.status === 'error') {
                            clearInterval(statusInterval);
                            showError(task.error || '处理失败');
                            resetUI();
                        }
                    }
                } catch (error) {
                    console.error('轮询失败:', error);
                }
            }, 2000);
        }
        
        function updateProgress(progress, message) {
            document.getElementById('progressBar').style.width = progress + '%';
            document.getElementById('progressText').textContent = message;
        }
        
        function showResult(result) {
            document.getElementById('resultContainer').classList.add('active');
            document.getElementById('resultTitle').textContent = result.title;
            
            if (result.smart_notes) {
                document.getElementById('resultTextNotes').innerHTML = formatMarkdown(result.smart_notes);
                document.getElementById('resultTextRaw').textContent = result.raw_text || '';
                switchResultTab('notes', document.querySelector('.result-tab-btn'));
            } else {
                document.getElementById('resultTextNotes').textContent = result.text || '';
                document.getElementById('resultTextRaw').style.display = 'none';
            }
        }
        
        function switchResultTab(tab, btn) {
            document.querySelectorAll('.result-tab-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            
            if (tab === 'notes') {
                document.getElementById('resultTextNotes').style.display = 'block';
                document.getElementById('resultTextRaw').style.display = 'none';
            } else {
                document.getElementById('resultTextNotes').style.display = 'none';
                document.getElementById('resultTextRaw').style.display = 'block';
            }
        }
        
        function formatMarkdown(text) {
            return text
                .replace(/^# (.+)$/gm, '<h1>$1</h1>')
                .replace(/^## (.+)$/gm, '<h2>$1</h2>')
                .replace(/^> (.+)$/gm, '<blockquote>$1</blockquote>')
                .replace(/^(\d+)\. (.+)$/gm, '<li>$2</li>')
                .replace(/`([^`]+)`/g, '<code>$1</code>')
                .replace(/^---$/gm, '<hr>')
                .replace(/\n\n/g, '</p><p>')
                .replace(/\n/g, '<br>');
        }
        
        function showError(message) {
            document.getElementById('errorContainer').classList.add('active');
            document.getElementById('errorText').textContent = message;
        }
        
        function resetUI() {
            document.getElementById('startBtn').disabled = false;
            document.getElementById('startBtn').textContent = '开始转写';
            document.getElementById('progressContainer').classList.remove('active');
        }
        
        // 回车键提交
        document.getElementById('videoUrl').addEventListener('keypress', function(e) {
            if (e.key === 'Enter') {
                startTranscribe();
            }
        });
    </script>
</body>
</html>
'''

if __name__ == '__main__':
    print("🚀 NoteForge Web服务启动中...")
    print("📱 请在浏览器中访问: http://localhost:5000")
    print("🛑 按 Ctrl+C 停止服务")
    app.run(host='0.0.0.0', port=5000, debug=False)
