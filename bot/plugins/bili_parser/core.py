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
import hashlib
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
API_NAV = "https://api.bilibili.com/x/web-interface/nav"
API_PLAYURL = "https://api.bilibili.com/x/player/wbi/playurl"

UA = "curl/8.5.0"  # view/spi 等接口用（curl UA 实测可过 412 风控）
UA_BROWSER = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)  # playurl 用完整浏览器指纹（wbi 接口实测可用）
REFERER = "https://www.bilibili.com/"

# wbi 签名参数（mixin key 置换表，B站公开算法）
MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40, 61,
    26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36,
    20, 34, 44, 52,
]
# wbi key 兜底（2026-09 验证有效；优先从 nav 动态获取）
_FALLBACK_WBI_KEYS = (
    "7cd084941338484aae1ad9425b84077c",
    "4932caff0ff746eab6f01bf08b70ac45",
)

DOWNLOAD_DIR = "/downloads"
DOWNLOAD_TIMEOUT = 600  # 单个视频下载总超时（秒）

# bili-api 服务地址（compose 内网服务名）
BILI_API_URL = os.environ.get("BILI_API_URL", "http://bili-api:8000")

# buvid cookie 缓存（TTL 1 小时，避免每次请求都打 spi）
_cookie_cache: Dict[str, object] = {"cookie": None, "ts": 0.0}
COOKIE_TTL = 3600

# playurl 被风控后的全局冷却（窗口限流，撞墙期反复请求只会延长封禁）
_playurl_cooldown_until: float = 0.0
PLAYURL_COOLDOWN_SECONDS = 600


def _in_playurl_cooldown() -> bool:
    return time.time() < _playurl_cooldown_until


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
            # B23_RE 无捕获组，兼容处理：能取到 group(1) 才用，否则整体返回
            try:
                g1 = m.group(1)
            except IndexError:
                return m.group(0)
            return g1 if g1.startswith("BV") else m.group(0)
    return None


def _http_get_json(url: str, headers: Optional[Dict] = None, timeout: float = 10.0) -> Dict:
    req = urllib.request.Request(
        url, headers=headers or {"User-Agent": UA, "Referer": REFERER}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _http_get_text(url: str, timeout: float = 30.0) -> str:
    """GET 并返回纯文本（bili-api 返回 text/plain）。"""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


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


def _get_buvid_cookie(force: bool = False) -> Optional[str]:
    """从 spi 接口获取 buvid3/buvid4 cookie（默认带缓存，force 强制刷新）。"""
    now = time.time()
    if (
        not force
        and _cookie_cache["cookie"]
        and now - _cookie_cache["ts"] < COOKIE_TTL
    ):
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


def _get_wbi_keys() -> Tuple[str, str]:
    """获取 wbi img/sub key：优先从 nav 动态获取（未登录也返回），失败用兜底。"""
    try:
        payload = _http_get_json(API_NAV)
        wbi = (payload.get("data") or {}).get("wbi_img") or {}
        img_key = (wbi.get("img_url") or "").rsplit("/", 1)[-1].split(".")[0]
        sub_key = (wbi.get("sub_url") or "").rsplit("/", 1)[-1].split(".")[0]
        if len(img_key) == 32 and len(sub_key) == 32:
            return img_key, sub_key
    except Exception:
        pass
    return _FALLBACK_WBI_KEYS


def _sign_wbi(params: Dict, img_key: str, sub_key: str) -> Dict:
    """按 B站 wbi 算法签名参数，返回带 wts / w_rid 的新参数字典。"""
    signed = dict(params)
    signed["wts"] = int(time.time())
    mixin = "".join((img_key + sub_key)[i] for i in MIXIN_KEY_ENC_TAB)[:32]
    qs = urllib.parse.urlencode(sorted(signed.items()))
    signed["w_rid"] = hashlib.md5((qs + mixin).encode()).hexdigest()
    return signed


async def fetch_playurl(
    bvid: str, cid: int, cookie: str, referer: Optional[str] = None
) -> Optional[Dict]:
    """调用 wbi 签名版 playurl 接口获取 dash 音视频流（完整浏览器指纹请求）。"""
    params = {"bvid": bvid, "cid": cid, "fnval": 16, "fourk": 0}
    img_key, sub_key = _get_wbi_keys()
    signed = _sign_wbi(params, img_key, sub_key)
    url = f"{API_PLAYURL}?{urllib.parse.urlencode(sorted(signed.items()))}"

    def _do():
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": UA_BROWSER,
                "Referer": referer or f"https://www.bilibili.com/video/{bvid}",
                "Cookie": cookie,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Origin": "https://www.bilibili.com",
            },
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
    cid: Optional[int] = None,
    out_dir: str = DOWNLOAD_DIR,
    timeout: int = DOWNLOAD_TIMEOUT,
) -> Tuple[Optional[str], str]:
    """通过 bili-api 服务下载 B站视频为 mp4。

    bili-api 抓取视频页 __playinfo__ 获取流地址（绕开 playurl API 风控），
    后台任务下载 + ffmpeg 合并。文件落在共享 downloads 目录
    （bili-api 的 /app/downloads == 本容器的 /downloads）。

    返回 (mp4路径, 错误信息)：成功时错误信息为空字符串。
    """
    safe = re.sub(r'[\\/:*?"<>|\s]+', "_", title).strip("_")[:40] or bvid
    filename = f"{bvid}_{safe}"
    # 加时间戳让每次请求 URL 唯一，绕过 bili-api 的按 URL 去重（409）
    page_url = f"https://www.bilibili.com/video/{bvid}?t={int(time.time() * 1000)}"
    params = {"url": page_url, "merge": "true", "filename": filename}
    api_url = f"{BILI_API_URL}/api/video/download?{urllib.parse.urlencode(params)}"

    try:
        resp_text = await asyncio.to_thread(_http_get_text, api_url)
        m = re.search(r"任务ID:\s*([0-9a-fA-F-]+)", resp_text)
        if not m:
            return None, f"未获取到任务ID: {resp_text[:200]}"
        task_id = m.group(1)
    except urllib.error.HTTPError as e:
        # 409 = 同 URL 任务已存在（时间戳碰撞等极小概率），复用已有任务
        if e.code == 409:
            body = ""
            try:
                body = e.read().decode("utf-8", "replace")
            except Exception:
                pass
            m = re.search(r"现有任务ID:\s*([0-9a-fA-F-]+)", body) or re.search(
                r"任务ID:\s*([0-9a-fA-F-]+)", body
            )
            if m:
                task_id = m.group(1)
            else:
                return None, f"bili-api 409 且无法解析任务ID: {body[:200]}"
        else:
            return None, f"bili-api 请求失败: {e!r}"
    except Exception as e:
        return None, f"bili-api 请求失败: {e!r}"

    deadline = time.time() + timeout
    while time.time() < deadline:
        await asyncio.sleep(5)
        try:
            st = await asyncio.to_thread(
                _http_get_text, f"{BILI_API_URL}/api/download/status/{task_id}"
            )
        except Exception:
            continue
        upper = st.upper()
        if "已完成" in st or "COMPLETED" in upper:
            m2 = re.search(r"合并文件:\s*(\S+)", st) or re.search(
                r"文件路径:\s*(\S+)", st
            )
            if m2:
                path = os.path.basename(m2.group(1).strip())
                return os.path.join(out_dir, path), ""
            return None, "任务完成但未找到文件路径"
        if "失败" in st or "FAILED" in upper:
            return None, f"下载任务失败: {st[:300]}"
    return None, f"下载超时（>{timeout}s）"


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
