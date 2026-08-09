# -*- coding: utf-8 -*-
"""
NoteForge 视频转写工具 v1.0
基于阿里达摩院 FunASR 框架的中文语音识别模型

功能:
- 支持单集/批量视频转写
- 自动提取音频、分段处理
- 内存优化，支持断点续传
- 输出带时间戳的文本文件

用法:
    python -m noteforge.sources.asr ep08          # 转写第8集
    python -m noteforge.sources.asr all           # 批量转写所有集
    python -m noteforge.sources.asr ep01 ep03     # 转写指定多集
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

# ASR 模型显式 pin（P1: 2026-08-09 实测发现）
# FunASR 1.3.x 的 "paraformer-zh" 短名已映射到 speech_seaco_paraformer_large
# （SEACO 大模型，CPU RTF ~0.35，比标准模型慢 3-4 倍）。
# 必须用全 ID 锁定标准 paraformer-large（RTF ~0.1，CPU 友好）。
# 可用环境变量 NOTEFORGE_ASR_MODEL 覆盖（例如想用 SEACO 时）。
_DEFAULT_ASR_MODEL = "iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch"
_DEFAULT_ASR_MODEL_OVERRIDE = os.environ.get('NOTEFORGE_ASR_MODEL', '')

# 断点续传分段时长（分钟）：每段完成后立即落盘，进程被杀可续传
_DEFAULT_SEGMENT_MINUTES = 10

# 在模块级别确保 HOME 可用（不依赖父进程环境变量）
# FunASR / HuggingFace 需要 HOME 定位模型缓存目录 (~/.cache/modelscope/)
# 某些场景（nohup、服务进程）下父进程可能不传递 HOME/USERPROFILE
if sys.platform == 'win32' and not os.environ.get('HOME', ''):
    try:
        _user_home = os.path.expanduser('~')
        if _user_home and _user_home != '~' and os.path.isdir(_user_home):
            os.environ['HOME'] = _user_home
            os.environ['USERPROFILE'] = _user_home
    except Exception:
        pass


def _ensure_console_encoding():
    """修复 Windows 控制台编码问题（仅在直接运行时执行，import 时跳过）。"""
    if hasattr(sys.stdout, 'reconfigure') and sys.stdout.isatty():
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')


def get_base_dir():
    """获取项目根目录"""
    return Path(__file__).parent.parent.parent


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


def _extract_result_text(result) -> str:
    """从 FunASR generate 结果中提取纯文本"""
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


def _split_audio_segments(audio_path: str, seg_seconds: int,
                          out_dir, base_name: str, duration: float):
    """用 ffmpeg 将音频切分为定长 wav 段（含 VAD 的识别精度可接受）

    Returns:
        [(seg_path, start_sec, end_sec), ...] 或 None（ffmpeg 失败时回退整体识别）
    """
    import math
    out_dir.mkdir(parents=True, exist_ok=True)
    pattern = str(out_dir / f"{base_name}_%05d.wav")
    cmd = [
        'ffmpeg', '-y', '-i', audio_path,
        '-f', 'segment', '-segment_time', str(seg_seconds),
        '-ar', '16000', '-ac', '1', '-c:a', 'pcm_s16le',
        pattern,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=600,
        )
        if result.returncode != 0:
            logger.warning("ffmpeg 切分失败: %s", result.stderr[-300:])
            return None
    except Exception as e:
        logger.warning("ffmpeg 切分失败，回退到整体识别: %s", e)
        return None

    files = sorted(out_dir.glob(f"{base_name}_*.wav"))
    if not files:
        logger.warning("ffmpeg 切分未产生段文件，回退到整体识别")
        return None

    n = len(files)
    segments = []
    for i, f in enumerate(files):
        start = i * seg_seconds
        end = min((i + 1) * seg_seconds, duration) if duration > 0 else start + seg_seconds
        segments.append((str(f), start, end))
    logger.info("音频已切分为 %d 段 (每段 %d秒)", n, seg_seconds)
    return segments


def _transcribe_with_checkpoint(model, audio_path: str, output_path: str,
                                batch_size: int, segment_minutes: int,
                                resume: bool, duration: float) -> str:
    """分段断点续传转写。

    每段完成后：
      - 立即追加到 output_path（增量可见）
      - 更新 output_path + '.progress.json'（记录已完成段与文本）
    中断后重跑（resume=True）：跳过已完成段，只识别剩余段。

    Returns:
        完整转写文本
    """
    import math
    seg_seconds = segment_minutes * 60
    progress_path = Path(output_path).with_suffix(
        Path(output_path).suffix + '.progress.json'
    )
    out_dir = get_base_dir() / "temp" / "_asr_segments"
    base_name = (Path(output_path).stem.replace(' ', '_'))[:60] or 'asr'

    segments = _split_audio_segments(audio_path, seg_seconds, out_dir, base_name, duration)
    if segments is None:
        # ffmpeg 不可用 → 回退到整体识别
        result = model.generate(input=audio_path, batch_size_s=batch_size, hotword='')
        return _extract_result_text(result)

    n = len(segments)
    total_seconds = duration if duration > 0 else n * seg_seconds

    # 加载已完成的段
    completed: dict = {}  # idx -> text
    if resume and progress_path.exists():
        try:
            with open(progress_path, 'r', encoding='utf-8') as f:
                prog = json.load(f)
            if (prog.get('audio_path') == audio_path
                    and prog.get('total_segments') == n):
                completed = {int(k): v for k, v in prog.get('texts', {}).items()}
                logger.info(
                    "断点续传: 已跳过 %d/%d 段 (%.1f%%)",
                    len(completed), n, 100.0 * len(completed) / n,
                )
            else:
                logger.info("进度文件与当前音频不匹配，从头开始")
        except Exception as e:
            logger.warning("进度文件读取失败，从头开始: %s", e)

    all_start = time.time()
    for idx in range(n):
        if idx in completed:
            continue
        seg_file, start, end = segments[idx]
        seg_len = max(1.0, end - start)
        seg_label = f"[{idx + 1}/{n}] ({int(start // 60)}分-{int(end // 60)}分)"
        logger.info("识别段 %s...", seg_label)
        seg_start = time.time()
        try:
            result = model.generate(input=seg_file, batch_size_s=batch_size, hotword='')
            text = _extract_result_text(result)
        except Exception as e:
            logger.error("段 %d 识别失败: %s", idx + 1, e)
            raise
        completed[idx] = text
        # 落盘：输出文件 + 进度文件
        _flush_checkpoint(output_path, progress_path, completed, n,
                          audio_path, segment_minutes, duration)
        elapsed = time.time() - seg_start
        seg_rtf = elapsed / seg_len
        done = len(completed)
        remaining = n - done
        eta = (elapsed / seg_len) * total_seconds * remaining / 60 if remaining > 0 else 0
        logger.info(
            "段 %d 完成 (%d字, %d秒, RTF=%.2f) | %d/%d 段, 预计还需 ~%d分",
            idx + 1, len(text), int(elapsed), seg_rtf, done, n, int(eta),
        )

    # 全部完成 → 标记 complete，清理段文件
    _flush_checkpoint(output_path, progress_path, completed, n,
                      audio_path, segment_minutes, duration, complete=True)
    try:
        for f in out_dir.glob(f"{base_name}_*.wav"):
            f.unlink(missing_ok=True)
    except Exception:
        pass

    elapsed = time.time() - all_start
    rtf = elapsed / total_seconds if total_seconds > 0 else 0
    logger.info("分段识别完成，%d 段，耗时 %d秒 (RTF=%.2f)", n, int(elapsed), rtf)

    return "\n".join(completed[i] for i in range(n) if completed.get(i)).strip()


def _flush_checkpoint(output_path: str, progress_path, completed: dict,
                      total_segments: int, audio_path: str,
                      segment_minutes: int, duration: float,
                      complete: bool = False):
    """落盘当前进度：输出文件 + progress.json"""
    text = "\n".join(
        completed[i] for i in range(total_segments) if completed.get(i)
    ).strip()
    try:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(text)
        with open(progress_path, 'w', encoding='utf-8') as f:
            json.dump({
                'audio_path': audio_path,
                'duration': duration,
                'total_segments': total_segments,
                'segment_minutes': segment_minutes,
                'completed': sorted(int(k) for k in completed),
                'texts': {str(k): v for k, v in completed.items()},
                'complete': complete,
            }, f, ensure_ascii=False, indent=1)
    except Exception as e:
        logger.error("进度落盘失败: %s", e)


def transcribe_with_paraformer(audio_path: str, chunk_duration: int = 60,
                                disable_speaker: bool = False,
                                output_path: str = None,
                                resume: bool = True,
                                segment_minutes: int = None):
    """
    使用 Paraformer 模型进行语音识别

    Args:
        audio_path: 音频文件路径
        chunk_duration: 分段时长(秒)，默认60秒（保留兼容参数）
        disable_speaker: 禁用说话人识别（单人内容可关闭，大幅提速CPU推理）
        output_path: 指定输出文件路径。提供时启用分段断点续传。
        resume: 是否从上次进度续传（默认 True）
        segment_minutes: 断点续传分段时长(分钟)，默认 _DEFAULT_SEGMENT_MINUTES

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

    # P1: 模型显式 pin — paraformer-zh 短名已指向 SEACO 大模型（慢 3-4 倍）
    model_id = _DEFAULT_ASR_MODEL_OVERRIDE or _DEFAULT_ASR_MODEL
    logger.info("正在加载 Paraformer 模型 (%s)...", model_id.split('/')[-1])
    model_start = time.time()

    model_kwargs = {
        "model": model_id,
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

    # P1: 分段断点续传（output_path 提供时启用）
    if output_path:
        seg_min = segment_minutes if segment_minutes and segment_minutes > 0 else _DEFAULT_SEGMENT_MINUTES
        return _transcribe_with_checkpoint(
            model, audio_path, output_path, batch_size, seg_min, resume, duration,
        )

    # 单次整体识别（无 output_path 时的兼容路径）
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

    return _extract_result_text(result)


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


def process_episode(ep_num: str, config: dict, disable_speaker: bool = False) -> bool:
    """
    处理单集视频

    Args:
        ep_num: 集数编号 (如 "ep08")
        config: 配置字典
        disable_speaker: 是否禁用说话人识别

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

    logger.info("[Step 2/3] Paraformer 识别中（分段断点续传）...")
    try:
        output_file = get_base_dir() / "output" / "transcripts" / f"{ep_num}.txt"
        text = transcribe_with_paraformer(
            audio_path, disable_speaker=disable_speaker,
            output_path=str(output_file), resume=True,
        )

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

    # 先确定输出路径（分段断点续传需要实时落盘到 output_path）
    output_dir = get_base_dir() / "output" / "transcripts"
    output_dir.mkdir(parents=True, exist_ok=True)
    # --output 传入的是完整路径，直接使用；否则拼接文件名
    if output_name and os.path.isabs(output_name):
        output_file = Path(output_name)
    else:
        output_file = output_dir / f"{output_name}.txt"
    output_file.parent.mkdir(parents=True, exist_ok=True)

    logger.info("[Step 2/2] Paraformer 识别中（分段断点续传）...")
    try:
        text = transcribe_with_paraformer(
            audio_path, disable_speaker=disable_speaker,
            output_path=str(output_file), resume=True,
        )

        if not text:
            logger.warning("识别结果为空")
            return False

    except Exception as e:
        logger.error("识别失败: %s", e, exc_info=True)
        return False

    logger.info("保存结果...")

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
    _ensure_console_encoding()
    print("="*70)
    print("  NoteForge v1.0 - 智能笔记锻造系统")
    print("  基于阿里达摩院 FunASR - 中文语音识别专家")
    print("="*70)
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}\n")

    # 解析参数（支持 --no-speaker 和 --output 标志）
    raw_args = sys.argv[1:]
    disable_speaker = '--no-speaker' in raw_args
    output_path = None
    args = []
    i = 0
    while i < len(raw_args):
        if raw_args[i] == '--output' and i + 1 < len(raw_args):
            output_path = raw_args[i + 1]
            i += 2
        else:
            args.append(raw_args[i])
            i += 1

    if not args:
        print("用法:")
        print("  python -m noteforge.sources.asr audio.wav          # 转写音频文件")
        print("  python -m noteforge.sources.asr ep08               # 转写第8集")
        print("  python -m noteforge.sources.asr all                # 批量转写所有集")
        print("  python -m noteforge.sources.asr ep01 ep03          # 转写指定多集")
        print("  --no-speaker        禁用说话人识别（提速，适合单人内容）")
        return

    ensure_dirs()

    first_arg = args[0]

    if os.path.isfile(first_arg) and first_arg.lower().endswith(('.wav', '.mp3', '.m4a', '.flac')):
        # --output 优先（完整路径），其次 positional arg
        output_name = output_path or (args[1] if len(args) > 1 else None)
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

        success = process_episode(ep_num, config, disable_speaker=disable_speaker)

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
