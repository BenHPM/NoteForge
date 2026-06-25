"""
bilibili_download.py — Bilibili 音频下载器

双策略:
  1. 优先 yt-dlp (需要 cookies)
  2. 降级 Bilibili API 直接下载 (无需 cookies，但可能被限速)

用法:
    python bilibili_download.py "https://www.bilibili.com/video/BV1xxx"
    python bilibili_download.py "BV1xxx"  # 自动补全 URL
    python bilibili_download.py "BV1xxx" --output "episode01.m4a"
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

SCRIPT_DIR = Path(__file__).parent
BASE_DIR = SCRIPT_DIR.parent
TEMP_DIR = BASE_DIR / "temp"
TEMP_DIR.mkdir(exist_ok=True)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def normalize_url(url_or_bvid: str) -> str:
    """规范化 URL：BV 号自动补全为完整 URL"""
    if url_or_bvid.startswith("BV"):
        return f"https://www.bilibili.com/video/{url_or_bvid}"
    if "b23.tv" in url_or_bvid:
        # 短链接解析
        try:
            import urllib.request
            req = urllib.request.Request(url_or_bvid, headers={"User-Agent": USER_AGENT})
            req.method = "HEAD"
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.url
        except Exception:
            pass
    return url_or_bvid


def extract_bvid(url: str) -> str:
    """从 URL 提取 BV 号"""
    match = re.search(r'(BV[a-zA-Z0-9]+)', url)
    return match.group(1) if match else ""


def get_video_info(bvid: str) -> dict:
    """通过 Bilibili API 获取视频信息"""
    import urllib.request
    api_url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
    req = urllib.request.Request(api_url, headers={
        "User-Agent": USER_AGENT,
        "Referer": "https://www.bilibili.com",
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode('utf-8'))
    if data.get('code') != 0:
        raise RuntimeError(f"Bilibili API 错误: {data.get('message', data)}")
    return data['data']


def get_audio_url(bvid: str, cid: int) -> str:
    """获取音频流 URL"""
    import urllib.request
    api_url = (
        f"https://api.bilibili.com/x/player/playurl"
        f"?bvid={bvid}&cid={cid}&fnval=16&qn=64"
    )
    req = urllib.request.Request(api_url, headers={
        "User-Agent": USER_AGENT,
        "Referer": f"https://www.bilibili.com/video/{bvid}",
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode('utf-8'))
    if data.get('code') != 0:
        raise RuntimeError(f"播放地址获取失败: {data.get('message', data)}")

    audio_streams = data['data'].get('dash', {}).get('audio', [])
    if not audio_streams:
        raise RuntimeError("未找到音频流")
    return audio_streams[0]['baseUrl']


def download_audio(url: str, output_path: str) -> bool:
    """下载音频文件（使用临时文件，避免中断后残留半截文件）"""
    import urllib.request
    tmp_path = output_path + '.tmp'
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT,
            "Referer": "https://www.bilibili.com",
        })
        with urllib.request.urlopen(req, timeout=120) as resp:
            total = int(resp.headers.get('Content-Length', 0))
            downloaded = 0
            with open(tmp_path, 'wb') as f:
                while True:
                    chunk = resp.read(1024 * 1024)  # 1MB chunks
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total > 0:
                        pct = downloaded / total * 100
                        mb = downloaded / 1024 / 1024
                        print(f"\r  下载中: {mb:.1f}MB ({pct:.0f}%)", end='', flush=True)
        print()
        # 下载完成后校验并重命名
        if os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 0:
            os.replace(tmp_path, output_path)
            return True
        else:
            # 空文件，清理
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            return False
    except Exception:
        # 下载失败，清理临时文件
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
        raise


def _find_cookies_file() -> str:
    """查找最佳 cookies 文件。

    优先级：
    1. cookies_all.txt（多站点导出，通常含 B 站 cookies）
    2. cookies*.txt 中包含 bilibili 关键词的
    3. 第一个 cookies*.txt
    """
    import glob as _glob
    for search_dir in [TEMP_DIR, BASE_DIR]:
        candidates = _glob.glob(str(search_dir / "cookies*.txt"))
        if not candidates:
            continue
        # 优先选 cookies_all.txt
        for c in candidates:
            if os.path.basename(c) == "cookies_all.txt":
                return c
        # 检查哪个文件包含 bilibili cookies
        for c in candidates:
            try:
                with open(c, 'r', encoding='utf-8', errors='replace') as f:
                    content = f.read(5000)  # 只读前 5KB
                    if 'bilibili.com' in content:
                        return c
            except Exception:
                continue
        # 回退：取第一个
        return candidates[0]
    return ""


def try_ytdlp(url: str, output_path: str) -> bool:
    """尝试 yt-dlp 下载（需要 cookies）"""
    cookies_path = _find_cookies_file()

    cmd = ["yt-dlp", "--no-update", "--extract-audio", "--audio-format", "m4a",
           "-o", output_path, url]
    if cookies_path:
        cmd.extend(["--cookies", cookies_path])
    else:
        # 尝试从 Edge 浏览器提取
        cmd.extend(["--cookies-from-browser", "edge"])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0 and os.path.exists(output_path):
            return True
        # DPAPI 或 412 错误时降级
        if "412" in result.stderr or "DPAPI" in result.stderr:
            return False
        return False
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def download_bilibili(url_or_bvid: str, output_path: str = None) -> dict:
    """
    下载 Bilibili 视频音频。

    Returns:
        {"success": bool, "path": str, "title": str, "duration": int, "method": str}
    """
    url = normalize_url(url_or_bvid)
    bvid = extract_bvid(url)
    if not bvid:
        return {"success": False, "error": f"无法提取 BV 号: {url}"}

    # 获取视频信息
    try:
        info = get_video_info(bvid)
    except Exception as e:
        return {"success": False, "error": f"获取视频信息失败: {e}"}

    title = info.get('title', bvid)
    duration = info.get('duration', 0)
    cid = info.get('cid', 0)

    if not output_path:
        safe_title = re.sub(r'[\\/:*?"<>|]', '_', title)
        output_path = str(TEMP_DIR / f"{safe_title}.m4a")

    # 如果音频文件已存在且大小合理，跳过下载
    if os.path.exists(output_path):
        file_size = os.path.getsize(output_path)
        # 简单校验：音频文件至少 10KB/分钟（约 1.3kbps 的最低码率）
        min_expected = max(duration * 1024, 10240)  # 至少 10KB
        if file_size >= min_expected:
            print(f"  [SKIP] 音频已存在，跳过下载: {os.path.basename(output_path)}")
            return {"success": True, "path": output_path, "title": title,
                    "duration": duration, "method": "cached"}
        else:
            print(f"  [WARN] 已有文件过小 ({file_size}B < {min_expected}B)，重新下载")

    print(f"  标题: {title}")
    print(f"  时长: {duration // 60}分{duration % 60}秒")
    print(f"  BV号: {bvid}")

    # 策略 1: yt-dlp
    print("\n  [策略1] yt-dlp 下载...")
    if try_ytdlp(url, output_path):
        print(f"  [OK] yt-dlp 成功: {output_path}")
        return {"success": True, "path": output_path, "title": title,
                "duration": duration, "method": "yt-dlp"}
    print("  [SKIP] yt-dlp 失败，降级到 API...")

    # 策略 2: Bilibili API
    print("\n  [策略2] Bilibili API 下载...")
    try:
        audio_url = get_audio_url(bvid, cid)
        print(f"  音频流: {audio_url[:60]}...")
        if download_audio(audio_url, output_path):
            print(f"  [OK] API 下载成功: {output_path}")
            return {"success": True, "path": output_path, "title": title,
                    "duration": duration, "method": "bilibili-api"}
    except Exception as e:
        print(f"  [ERROR] API 下载失败: {e}")

    return {"success": False, "error": "所有下载策略均失败"}


def main():
    parser = argparse.ArgumentParser(description='Bilibili 音频下载器')
    parser.add_argument('url', help='Bilibili 视频 URL 或 BV 号')
    parser.add_argument('--output', '-o', help='输出文件路径')
    args = parser.parse_args()

    result = download_bilibili(args.url, args.output)

    if result['success']:
        size_mb = os.path.getsize(result['path']) / 1024 / 1024
        print(f"\n  下载完成: {result['path']} ({size_mb:.1f}MB)")
        print(f"  方法: {result['method']}")
    else:
        print(f"\n  下载失败: {result.get('error', '未知错误')}")
        sys.exit(1)


if __name__ == '__main__':
    main()
