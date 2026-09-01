"""B站视频解析核心逻辑。

刻意不依赖 NoneBot / httpx：仅用 Python 标准库，可独立测试、复用，
本地无需安装任何第三方包即可跑通（见根目录 demo.py）。

函数分两类：
- 同步纯函数：extract_bvid / format_duration / format_count / build_reply
- 异步网络函数：resolve_bvid（短链展开）/ fetch_video（调 B站 API）
"""

import asyncio
import base64
import json
import re
import urllib.parse
import urllib.request
from typing import Dict, List, Optional

BVID_RE = re.compile(r"(BV[0-9A-Za-z]{10})")
B23_RE = re.compile(r"https?://b23\.tv/[0-9A-Za-z]+", re.IGNORECASE)
BILI_LINK_RE = re.compile(
    r"https?://(?:www\.|m\.)?bilibili\.com/video/(BV[0-9A-Za-z]{10})",
    re.IGNORECASE,
)

API_VIEW = "https://api.bilibili.com/x/web-interface/view"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


def extract_bvid(
    text: str, json_payloads: Optional[List[str]] = None
) -> Optional[str]:
    """从消息文本中提取 BV 号或 b23.tv 短链。

    json_payloads：QQ 小程序卡片（OneBot json 段）里提取出的原始 JSON
    字符串列表，用于兼容"哔哩哔哩小程序卡片"形式的分享。
    返回完整链接中的 BV 号、纯 BV 号或短链（短链需再经 resolve_bvid 展开）。
    """
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


def _http_get_json(url: str, timeout: float = 10.0) -> Dict:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": UA, "Referer": "https://www.bilibili.com/"},
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
    """调用 B站公开接口获取视频信息（title/owner/stat/duration/pic）。"""
    try:
        url = f"{API_VIEW}?{urllib.parse.urlencode({'bvid': bvid})}"
        payload = await asyncio.to_thread(_http_get_json, url)
        if payload.get("code") != 0:
            return None
        return payload.get("data")
    except Exception:
        return None


async def download_image_base64(url: str, timeout: float = 10.0) -> Optional[str]:
    """下载图片并转为 base64:// 数据（带 B站 Referer 绕过防盗链）。

    返回可直接用于 MessageSegment.image 的 base64:// 字符串；失败返回 None。
    """
    try:

        def _do():
            req = urllib.request.Request(
                url,
                headers={"User-Agent": UA, "Referer": "https://www.bilibili.com/"},
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
    """把 B站 API 返回的视频信息拼成群消息文本。"""
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
