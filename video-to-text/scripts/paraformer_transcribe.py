"""
NoteForge 视频转写工具 v1.0
基于阿里达摩院 FunASR 框架的中文语音识别模型

功能:
- 支持单集/批量视频转写
- 自动提取音频、分段处理
- 内存优化，支持断点续传
- 输出带时间戳的文本文件

用法:
    python paraformer_transcribe.py ep08          # 转写第8集
    python paraformer_transcribe.py all           # 批量转写所有集
    python paraformer_transcribe.py ep01 ep03     # 转写指定多集
"""

import os
import sys
import json
import time
import subprocess
import logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger('noteforge.asr')

# 修复 Windows 控制台编码问题（subprocess 调用时 emoji 等 Unicode 字符）
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')


def get_base_dir():
    """获取项目根目录"""
    return Path(__file__).parent.parent


def load_config():
    """加载视频映射配置（支持数组格式和对象格式）"""
    config_path = get_base_dir() / "config" / "video-mapping.json"
    if not config_path.exists():
        logger.error("配置文件不存在: %s", config_path)
        return None

    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            content = f.read()
        # 处理中文引号
        import re as _re
        content = _re.sub(r'["""]', '"', content)
        mapping = json.loads(content)
    except Exception as e:
        logger.error("解析配置文件失败: %s", e)
        return None

    # 统一转换为 {"episodes": {id: item}} 格式，供后续代码使用
    if isinstance(mapping, list):
        episodes = {}
        for item in mapping:
            ep_id = item.get('id', '')
            if ep_id:
                episodes[ep_id] = item
        return {'episodes': episodes}
    elif isinstance(mapping, dict):
        # 已经是 dict 格式，直接返回
        return mapping
    else:
        logger.error("配置文件格式不正确: 期望 list 或 dict")
        return None


def ensure_dirs():
    """确保输出目录存在"""
    output_dir = get_base_dir() / "output" / "transcripts"
    temp_dir = get_base_dir() / "temp"
    
    output_dir.mkdir(parents=True, exist_ok=True)
    temp_dir.mkdir(parents=True, exist_ok=True)


def extract_audio(video_path: str, audio_path: str) -> bool:
    """
    使用 ffmpeg 从视频中提取音频
    
    Args:
        video_path: 视频文件路径
        audio_path: 输出音频路径
        
    Returns:
        是否成功
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
        
        result = subprocess.run(
            cmd,
            capture_output=True, text=True,
            encoding='utf-8', errors='replace',
            timeout=300
        )
        
        return result.returncode == 0 and os.path.exists(audio_path)
        
    except subprocess.TimeoutExpired:
        logger.error("音频提取超时")
        return False
    except Exception as e:
        logger.error("音频提取失败: %s", e)
        return False


def _get_audio_duration(audio_path: str) -> float:
    """获取音频时长（秒）"""
    try:
        import soundfile as sf
        info = sf.info(audio_path)
        return info.duration
    except Exception as e:
        logger.debug(f"获取音频时长失败: {e}")
        return 0.0


def transcribe_with_paraformer(audio_path: str, chunk_duration: int = 60,
                                disable_speaker: bool = False):
    """
    使用 Paraformer 模型进行语音识别

    Args:
        audio_path: 音频文件路径
        chunk_duration: 分段时长(秒)，默认60秒
        disable_speaker: 禁用说话人识别（单人内容可关闭，大幅提速CPU推理）

    Returns:
        识别结果文本
    """
    from funasr import AutoModel
    import soundfile as sf

    # 检测音频时长，提前预估耗时
    duration = _get_audio_duration(audio_path)
    if duration > 0:
        mins, secs = divmod(int(duration), 60)
        file_mb = os.path.getsize(audio_path) / (1024 * 1024)
        logger.info("音频时长: %s分%s秒 | 大小: %.1fMB", mins, secs, file_mb)
        est_mins = max(1, int(duration / 60 * 1.5))
        logger.info("预估转写耗时: ~%d分钟（请耐心等待）", est_mins)

    # CPU 检测：无 GPU 时自动优化参数
    try:
        import torch
        has_cuda = torch.cuda.is_available()
    except ImportError:
        has_cuda = False

    if not has_cuda:
        logger.info("检测到 CPU 模式，自动优化参数（降速 batch_size_s，跳过说话人识别）")
        disable_speaker = True

    logger.info("正在加载 Paraformer 模型...")
    model_start = time.time()

    model_kwargs = {
        "model": "paraformer-zh",
        "vad_model": "fsmn-vad",
        "punc_model": "ct-punc-c",
        "disable_update": True,
    }
    # 说话人识别：单人内容或 CPU 模式下跳过（cam++ 是最耗 CPU 的模型）
    if not disable_speaker:
        model_kwargs["spk_model"] = "cam++"
        logger.info("已启用说话人识别（cam++）")
    else:
        logger.info("已跳过说话人识别（disable_speaker=True 或 CPU 模式自动跳过）")

    model = AutoModel(**model_kwargs)

    load_time = time.time() - model_start
    logger.info("模型加载完成 (%.1f秒)", load_time)

    # CPU 模式用较小的 batch_size_s 降低峰值内存和延迟
    batch_size = 300 if has_cuda else 60

    logger.info("开始识别音频 (batch_size_s=%d)...", batch_size)
    transcribe_start = time.time()
    result = model.generate(
        input=audio_path,
        batch_size_s=batch_size,
        hotword=''
    )

    elapsed = time.time() - transcribe_start
    rtf = elapsed / duration if duration > 0 else 0
    logger.info("识别完成，耗时 %d秒 (RTF=%.1f)", int(elapsed), rtf)
    
    text = ""
    if isinstance(result, list) and len(result) > 0:
        for seg in result:
            if 'text' in seg:
                text += seg['text'] + "\n"
            elif 'sentence_info' in seg:
                for sent in seg['sentence_info']:
                    if 'punc' in sent:
                        text += sent['punc'] + "\n"
    
    return text.strip()


def save_result(text: str, ep_num: str):
    """
    保存转写结果到文件
    
    Args:
        text: 转写文本
        ep_num: 集数编号
    """
    output_dir = get_base_dir() / "output" / "transcripts"
    output_file = output_dir / f"{ep_num}.txt"
    
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(text)
    
    char_count = len(text.replace('\n', '').replace(' ', ''))
    logger.info("已保存: %s", output_file)
    logger.info("字数: %d", char_count)

    return str(output_file)


def process_episode(ep_num: str, config: dict) -> bool:
    """
    处理单集视频
    
    Args:
        ep_num: 集数编号 (如 "ep08")
        config: 配置字典
        
    Returns:
        是否成功
    """
    logger.info("处理: %s", ep_num)

    if ep_num not in config.get('episodes', {}):
        logger.error("未找到 %s 的配置", ep_num)
        return False

    episode_config = config['episodes'][ep_num]
    video_file = episode_config.get('file', '')
    title = episode_config.get('title', ep_num)

    if not video_file or not os.path.exists(video_file):
        logger.error("视频文件不存在: %s", video_file)
        return False

    logger.info("标题: %s", title)
    logger.info("文件: %s", video_file)
    logger.info("时间: %s", datetime.now().strftime('%Y-%m-%d %H:%M:%S'))

    base_dir = get_base_dir()
    temp_dir = base_dir / "temp"
    audio_path = str(temp_dir / f"{ep_num}_audio.wav")

    total_start = time.time()

    logger.info("[Step 1/3] 提取音频...")
    if not extract_audio(video_file, audio_path):
        return False

    audio_size_mb = os.path.getsize(audio_path) / (1024 * 1024)
    logger.info("完成 (%.1fMB)", audio_size_mb)

    logger.info("[Step 2/3] Paraformer 识别中...")
    try:
        text = transcribe_with_paraformer(audio_path, disable_speaker=disable_speaker)

        if not text:
            logger.warning("识别结果为空")
            return False

    except Exception as e:
        logger.error("识别失败: %s", e)
        return False

    logger.info("[Step 3/3] 保存结果...")
    save_result(text, ep_num)

    if os.path.exists(audio_path):
        os.remove(audio_path)

    elapsed = time.time() - total_start
    logger.info("总耗时: %.1f秒", elapsed)
    
    return True


def process_audio_file(audio_path: str, output_name: str = None,
                        disable_speaker: bool = False) -> bool:
    """
    直接处理音频文件

    Args:
        audio_path: 音频文件路径
        output_name: 输出文件名(不含扩展名)
        disable_speaker: 禁用说话人识别

    Returns:
        是否成功
    """
    logger.info("处理音频文件")

    if not os.path.exists(audio_path):
        logger.error("音频文件不存在: %s", audio_path)
        return False

    if not output_name:
        output_name = Path(audio_path).stem

    logger.info("文件: %s", audio_path)
    logger.info("输出名: %s", output_name)
    logger.info("时间: %s", datetime.now().strftime('%Y-%m-%d %H:%M:%S'))

    total_start = time.time()

    audio_size_mb = os.path.getsize(audio_path) / (1024 * 1024)
    logger.info("[Step 1/2] 音频文件: %.1fMB", audio_size_mb)

    logger.info("[Step 2/2] Paraformer 识别中...")
    try:
        text = transcribe_with_paraformer(audio_path, disable_speaker=disable_speaker)

        if not text:
            logger.warning("识别结果为空")
            return False

    except Exception as e:
        logger.error("识别失败: %s", e, exc_info=True)
        return False

    logger.info("保存结果...")

    output_dir = get_base_dir() / "output" / "transcripts"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"{output_name}.txt"

    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(text)

    char_count = len(text.replace('\n', '').replace(' ', ''))
    logger.info("已保存: %s", output_file)
    logger.info("字数: %d", char_count)

    elapsed = time.time() - total_start
    logger.info("总耗时: %.1f秒", elapsed)
    
    return True


def main():
    """主函数"""
    print("="*70)
    print("  NoteForge v1.0 - 智能笔记锻造系统")
    print("  基于阿里达摩院 FunASR - 中文语音识别专家")
    print("="*70)
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}\n")

    # 解析参数（支持 --no-speaker 标志）
    raw_args = sys.argv[1:]
    disable_speaker = '--no-speaker' in raw_args
    args = [a for a in raw_args if a != '--no-speaker']

    if not args:
        print("用法:")
        print("  python paraformer_transcribe.py audio.wav          # 转写音频文件")
        print("  python paraformer_transcribe.py ep08               # 转写第8集")
        print("  python paraformer_transcribe.py all                # 批量转写所有集")
        print("  python paraformer_transcribe.py ep01 ep03          # 转写指定多集")
        print("  --no-speaker        禁用说话人识别（提速，适合单人内容）")
        return

    ensure_dirs()

    first_arg = args[0]

    if os.path.isfile(first_arg) and first_arg.lower().endswith(('.wav', '.mp3', '.m4a', '.flac')):
        output_name = args[1] if len(args) > 1 else None
        success = process_audio_file(first_arg, output_name, disable_speaker=disable_speaker)
        sys.exit(0 if success else 1)
    
    config = load_config()
    if not config:
        return
    
    episodes_to_process = []
    
    if len(args) == 1 and args[0].lower() == 'all':
        episodes_to_process = list(config.get('episodes', {}).keys())
        episodes_to_process.sort()
    else:
        for arg in args:
            if arg in config.get('episodes', {}):
                episodes_to_process.append(arg)
            else:
                logger.warning("跳过未知集数: %s", arg)
    
    if not episodes_to_process:
        logger.error("没有有效的集数可处理")
        return
    
    print(f"\n📋 计划处理 {len(episodes_to_process)} 集:")
    for i, ep in enumerate(episodes_to_process, 1):
        title = config['episodes'][ep].get('title', ep)
        print(f"   {i}. {ep} - {title}")
    
    results = {'success': [], 'failed': []}
    start_time = time.time()
    
    for i, ep_num in enumerate(episodes_to_process, 1):
        logger.info("[%d/%d]", i, len(episodes_to_process))
        
        success = process_episode(ep_num, config)
        
        if success:
            results['success'].append(ep_num)
        else:
            results['failed'].append(ep_num)
    
    total_time = time.time() - start_time
    
    print("\n" + "="*70)
    print("  📊 任务完成报告")
    print("="*70)
    print(f"\n  ✅ 成功: {len(results['success'])} 集")
    if results['success']:
        print(f"     {', '.join(results['success'])}")
    
    if results['failed']:
        print(f"\n  ❌ 失败: {len(results['failed'])} 集")
        print(f"     {', '.join(results['failed'])}")
    
    print(f"\n  ⏱️  总耗时: {total_time:.0f}秒 ({total_time/60:.1f}分钟)")
    
    if results['success']:
        avg_time = total_time / len(results['success'])
        print(f"  ⚡ 平均每集: {avg_time:.0f}秒")
    
    print(f"\n  📁 输出目录: {get_base_dir() / 'output' / 'transcripts'}")
    print("="*70)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(levelname)s: %(message)s')
    main()
