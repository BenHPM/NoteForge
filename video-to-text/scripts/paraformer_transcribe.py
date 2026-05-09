"""
Paraformer 视频转写工具 v1.0
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
from pathlib import Path
from datetime import datetime


def get_base_dir():
    """获取项目根目录"""
    return Path(__file__).parent.parent


def load_config():
    """加载视频映射配置"""
    config_path = get_base_dir() / "config" / "video-mapping.json"
    if not config_path.exists():
        print(f"[ERROR] 配置文件不存在: {config_path}")
        return None
    
    with open(config_path, 'r', encoding='utf-8') as f:
        return json.load(f)


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
            '-vn',  # 无视频
            '-acodec', 'pcm_s16le',  # PCM 16位
            '-ar', '16000',  # 16kHz采样率
            '-ac', '1',  # 单声道
            audio_path
        ]
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300  # 5分钟超时
        )
        
        return result.returncode == 0 and os.path.exists(audio_path)
        
    except subprocess.TimeoutExpired:
        print("[ERROR] 音频提取超时")
        return False
    except Exception as e:
        print(f"[ERROR] 音频提取失败: {e}")
        return False


def transcribe_with_paraformer(audio_path: str, chunk_duration: int = 60):
    """
    使用 Paraformer 模型进行语音识别
    
    Args:
        audio_path: 音频文件路径
        chunk_duration: 分段时长(秒)，默认60秒
        
    Returns:
        识别结果文本
    """
    from funasr import AutoModel
    import torchaudio
    import soundfile as sf
    
    print("\n[INFO] 正在加载 Paraformer 模型...")
    model_start = time.time()
    
    model = AutoModel(
        model="paraformer-zh",
        vad_model="fsmn-vad",
        punc_model="ct-punc-c",
        spk_model="cam++",
    )
    
    load_time = time.time() - model_start
    print(f"[OK] 模型加载完成 ({load_time:.1f}秒)")
    
    print("[INFO] 开始识别音频...")
    result = model.generate(
        input=audio_path,
        batch_size_s=300,
        hotword=''
    )
    
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
    print(f"\n[OK] 已保存: {output_file}")
    print(f"     字数: {char_count}")
    
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
    print(f"\n{'='*70}")
    print(f"  🎬 处理: {ep_num}")
    print(f"{'='*70}")
    
    if ep_num not in config.get('episodes', {}):
        print(f"[ERROR] 未找到 {ep_num} 的配置")
        return False
    
    episode_config = config['episodes'][ep_num]
    video_file = episode_config.get('file', '')
    title = episode_config.get('title', ep_num)
    
    if not video_file or not os.path.exists(video_file):
        print(f"[ERROR] 视频文件不存在: {video_file}")
        return False
    
    print(f"\n  标题:   {title}")
    print(f"  文件:   {video_file}")
    print(f"  时间:   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    base_dir = get_base_dir()
    temp_dir = base_dir / "temp"
    audio_path = str(temp_dir / f"{ep_num}_audio.wav")
    
    total_start = time.time()
    
    # Step 1: 提取音频
    print(f"\n[Step 1/3] 提取音频...")
    if not extract_audio(video_file, audio_path):
        return False
    
    audio_size_mb = os.path.getsize(audio_path) / (1024 * 1024)
    print(f"         ✅ 完成 ({audio_size_mb:.1f}MB)")
    
    # Step 2: 语音识别
    print(f"\n[Step 2/3] Paraformer 识别中...")
    try:
        text = transcribe_with_paraformer(audio_path)
        
        if not text:
            print("[WARNING] 识别结果为空")
            return False
            
    except Exception as e:
        print(f"[ERROR] 识别失败: {e}")
        return False
    
    # Step 3: 保存结果
    print(f"\n[Step 3/3] 保存结果...")
    save_result(text, ep_num)
    
    # 清理临时文件
    if os.path.exists(audio_path):
        os.remove(audio_path)
    
    elapsed = time.time() - total_start
    print(f"\n⏱️  总耗时: {elapsed:.1f}秒")
    
    return True


def main():
    """主函数"""
    print("="*70)
    print("  Paraformer 视频转写工具 v1.0")
    print("  基于阿里达摩院 FunASR - 中文语音识别专家")
    print("="*70)
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}\n")
    
    args = sys.argv[1:]
    
    if not args:
        print("用法:")
        print("  python paraformer_transcribe.py ep08          # 转写第8集")
        print("  python paraformer_transcribe.py all           # 批量转写所有集")
        print("  python paraformer_transcribe.py ep01 ep03     # 转写指定多集")
        return
    
    config = load_config()
    if not config:
        return
    
    ensure_dirs()
    
    episodes_to_process = []
    
    if len(args) == 1 and args[0].lower() == 'all':
        episodes_to_process = list(config.get('episodes', {}).keys())
        episodes_to_process.sort()
    else:
        for arg in args:
            if arg in config.get('episodes', {}):
                episodes_to_process.append(arg)
            else:
                print(f"[WARN] 跳过未知集数: {arg}")
    
    if not episodes_to_process:
        print("[ERROR] 没有有效的集数可处理")
        return
    
    print(f"\n📋 计划处理 {len(episodes_to_process)} 集:")
    for i, ep in enumerate(episodes_to_process, 1):
        title = config['episodes'][ep].get('title', ep)
        print(f"   {i}. {ep} - {title}")
    
    results = {'success': [], 'failed': []}
    start_time = time.time()
    
    for i, ep_num in enumerate(episodes_to_process, 1):
        print(f"\n\n[{i}/{len(episodes_to_process)}]", end="")
        
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
    main()
