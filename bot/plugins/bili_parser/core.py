"""B站视频解析核心逻辑。

刻意不依赖 NoneBot：仅用 Python 标准库 + 系统 ffmpeg，可独立测试、复用。

功能：
- 从消息提取 BV 号 / b23.tv 短链（含 QQ 小程序卡片）
- 调 view API 获取视频信息
- 调 playurl API 获取视频流，下载后经 ffmpeg 合并为 mp4
- 下载封面图转 base64（绕过防盗链）

注意：B站风控对"数据中心 IP + 浏览器 UA 但无 cookie"的请求返回 412，
实测 curl 风格 UA + buvid cookie 可稳定通过（2026-09 验证）。
"""

import asyncio
import base64
import json
import os
import re
import time
import urllib.parse
import urllib.request
from typing import Dict, List, Optional, Tuple

BVID_RE = re.compile(r"(BV[0-9A-Za-z]{10})")
B23_RE = re.compile(r"https?://b23\.tv/[0-9A-Za-z]+", re.IGNORECASE)
BILI_LINK_RE = re.compile(
    r"https?://(?:www\.|m\.)?bilibili\.com/video/(BV[0-9A-Za-z]{10})",
    re.IGNORECASE,
)

API_VIEW = "https://api.bilibili.com/x/web-interface/view"
API_SPI = "https://api.bilibili.com/x/frontend/finger/spi"
API_PLAYURL = "https://api.bilibili.com/x/player/playurl"

UA = "curl/8.5.0"
REFERER = "https://www.bilibili.com/"

DOWNLOAD_DIR = "/downloads"
DOWNLOAD_TIMEOUT = 600  # 单个视频下载/合并总超时（秒）

# buvid cookie 缓存（TTL 1 小时，避免每次请求都打 spi）
_cookie_cache: Dict[str, object] = {"cookie": None, "ts": 0.0}
COOKIE_TTL = 3600


def extract_bvid(
    text: str, json_payloads: Optional[List[str]] = None
) -> Optional[str]:
    """从消息文本中提取 BV 号或 b23.tv 短链。"""
    m = BILI_LINK_RE.search(text) or BVID_RE.search(text)
    if m:
        return m.group(1)
    m = B23_RE.search(text)
    if m:
        return m.group(0)
    for payload in json_payloads or []:
        m = (
            BILI_LINK_RE.search(payload)
            or B23_RE.search(payload)
            or BVID_RE.search(payload)
        )
        if m:
            return m.group(1) if m.group(1).startswith("BV") else m.group(0)
    return None


def _http_get_json(url: str, headers: Optional[Dict] = None, timeout: float = 10.0) -> Dict:
    req = urllib.request.Request(
        url, headers=headers or {"User-Agent": UA, "Referer": REFERER}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _resolve_redirect(url: str) -> str:
    """跟随重定向拿最终 URL（urllib 自动处理 302）。"""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA}, method="HEAD")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.geturl()
    except Exception:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.geturl()


async def resolve_bvid(raw: str) -> Optional[str]:
    """b23.tv 短链展开为真实 BV 号；非短链原样返回。"""
    if raw.startswith("http"):
        try:
            final_url = await asyncio.to_thread(_resolve_redirect, raw)
            m = BVID_RE.search(final_url)
            return m.group(1) if m else None
        except Exception:
            return None
    return raw


async def fetch_video(bvid: str) -> Optional[Dict]:
    """调用 view 接口获取视频信息（title/owner/stat/duration/pic/cid）。"""
    try:
        url = f"{API_VIEW}?{urllib.parse.urlencode({'bvid': bvid})}"
        payload = await asyncio.to_thread(_http_get_json, url)
        if payload.get("code") != 0:
            return None
        return payload.get("data")
    except Exception:
        return None


def _get_buvid_cookie() -> Optional[str]:
    """从 spi 接口获取 buvid3/buvid4 cookie（带缓存）。"""
    now = time.time()
    if _cookie_cache["cookie"] and now - _cookie_cache["ts"] < COOKIE_TTL:
        return _cookie_cache["cookie"]
    try:
        payload = _http_get_json(API_SPI)
        if payload.get("code") == 0:
            data = payload["data"]
            cookie = f"buvid3={data['b_3']}; buvid4={data['b_4']}"
            _cookie_cache.update(cookie=cookie, ts=now)
            return cookie
    except Exception:
        pass
    return None


async def fetch_playurl(bvid: str, cid: int, cookie: str) -> Optional[Dict]:
    """调用 playurl 接口获取 dash 音视频流（无需 wbi 签名，2026-09 验证）。"""
    params = urllib.parse.urlencode(
        {"bvid": bvid, "cid": cid, "fnval": 16, "fourk": 0}
    )
    url = f"{API_PLAYURL}?{params}"

    def _do():
        req = urllib.request.Request(
            url,
            headers={"User-Agent": UA, "Referer": REFERER, "Cookie": cookie},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))

    try:
        payload = await asyncio.to_thread(_do)
        if payload.get("code") == 0:
            return payload.get("data") or {}
    except Exception:
        pass
    return None


def _pick_streams(dash: Dict) -> Tuple[Optional[Dict], Optional[Dict]]:
    """从 dash 里选视频流（优先 <=480p 的最高，无则最低）和第一个音频流。"""
    videos = [
        v for v in (dash.get("video") or []) if v.get("baseUrl") or v.get("backupUrl")
    ]
    audios = [
        a for a in (dash.get("audio") or []) if a.get("baseUrl") or a.get("backupUrl")
    ]
    target = None
    if videos:
        videos.sort(key=lambda v: v.get("height") or 9999)
        target = next((v for v in videos if (v.get("height") or 9999) <= 480), videos[0])
    return target, (audios[0] if audios else None)


def _pick_url(stream: Optional[Dict]) -> Optional[str]:
    if not stream:
        return None
    return stream.get("baseUrl") or (stream.get("backupUrl") or [None])[0]


async def _download_file(url: str, path: str, cookie: str, timeout: float = 300) -> bool:
    """流式下载文件，返回是否成功。"""

    def _do():
        headers = {"User-Agent": UA, "Referer": REFERER}
        if cookie:
            headers["Cookie"] = cookie
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp, open(path, "wb") as f:
            while True:
                chunk = resp.read(1024 * 256)
                if not chunk:
                    break
                f.write(chunk)

    try:
        await asyncio.wait_for(asyncio.to_thread(_do), timeout=timeout)
        return os.path.getsize(path) > 0
    except Exception:
        return False


async def _merge_mp4(video_path: str, audio_path: Optional[str], out_path: str) -> bool:
    """ffmpeg -c copy 合并（不转码，快且省资源）。"""
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", video_path]
    if audio_path:
        cmd += ["-i", audio_path]
    cmd += ["-c", "copy", out_path]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        await proc.communicate()
        return proc.returncode == 0 and os.path.exists(out_path)
    except Exception:
        return False


async def download_video(
    bvid: str,
    title: str,
    cid: int,
    out_dir: str = DOWNLOAD_DIR,
    timeout: int = DOWNLOAD_TIMEOUT,
) -> Tuple[Optional[str], str]:
    """下载 B站视频为 mp4（<=480p，音视频分离流 + ffmpeg 合并）。

    返回 (mp4路径, 错误信息)：成功时错误信息为空字符串。
    """
    try:
        os.makedirs(out_dir, exist_ok=True)
    except OSError as e:
        return None, f"无法创建下载目录 {out_dir}: {e!r}"

    cookie = _get_buvid_cookie()
    if not cookie:
        return None, "获取 B站 cookie 失败"

    dash_data = await fetch_playurl(bvid, cid, cookie)
    if not dash_data:
        return None, "playurl 获取失败（可能被风控）"
    video_stream, audio_stream = _pick_streams(dash_data)
    if not video_stream:
        return None, "未找到可用的视频流"
    v_url = _pick_url(video_stream)
    a_url = _pick_url(audio_stream)
    if not v_url:
        return None, "视频流 URL 为空"

    safe = re.sub(r'[\\/:*?"<>|\s]+', "_", title).strip("_")[:40] or bvid
    base = f"{bvid}_{safe}"
    v_path = os.path.join(out_dir, f"{base}.video.m4s")
    a_path = os.path.join(out_dir, f"{base}.audio.m4s") if a_url else None

    if not await _download_file(v_url, v_path, cookie):
        return None, "视频流下载失败"
    if a_url and not await _download_file(a_url, a_path, cookie):
        return None, "音频流下载失败"

    mp4 = os.path.join(out_dir, f"{base}.mp4")
    ok = await _merge_mp4(v_path, a_path, mp4)
    for p in (v_path, a_path):
        if p and os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass
    if not ok:
        return None, "ffmpeg 合并失败"
    return mp4, ""


async def download_image_base64(url: str, timeout: float = 10.0) -> Optional[str]:
    """下载图片并转为 base64:// 数据（带 B站 Referer 绕过防盗链）。"""
    try:

        def _do():
            req = urllib.request.Request(
                url, headers={"User-Agent": UA, "Referer": REFERER}
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()

        data = await asyncio.to_thread(_do)
        if not data:
            return None
        return "base64://" + base64.b64encode(data).decode("ascii")
    except Exception:
        return None


def format_duration(seconds: int) -> str:
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def format_count(n: int) -> str:
    if n >= 100_000_000:
        return f"{n / 100_000_000:.1f}亿"
    if n >= 10_000:
        return f"{n / 10_000:.1f}万"
    return str(n)


def build_reply(info: Dict, bvid: str) -> str:
    """把 view 接口返回的视频信息拼成群消息文本。"""
    title = info.get("title", "未知标题")
    owner = (info.get("owner") or {}).get("name", "未知UP主")
    stat = info.get("stat") or {}
    duration = format_duration(info.get("duration", 0))
    url = f"https://www.bilibili.com/video/{bvid}"
    return (
        f"【B站视频】\n"
        f"标题：{title}\n"
        f"UP主：{owner}\n"
        f"时长：{duration} ｜ 播放：{format_count(stat.get('view', 0))} "
        f"｜ 点赞：{format_count(stat.get('like', 0))}\n"
        f"{url}"
    )
