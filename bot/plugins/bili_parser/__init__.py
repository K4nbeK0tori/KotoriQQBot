"""B站视频解析插件。

检测群消息中的哔哩哔哩链接（完整链接 / b23.tv 短链 / 纯 BV 号，
包括 QQ 小程序卡片形式的分享），调用 B站公开 API 获取视频信息，
以文字 + 封面图的形式回复到群里。

依赖：httpx（requirements.txt 已包含）
"""

import json
import re
import time
from typing import Dict, Optional, Tuple

import httpx
from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, MessageSegment

BVID_RE = re.compile(r"(BV[0-9A-Za-z]{10})")
B23_RE = re.compile(r"https?://b23\.tv/[0-9A-Za-z]+", re.IGNORECASE)
BILI_LINK_RE = re.compile(
    r"https?://(?:www\.|m\.)?bilibili\.com/video/(BV[0-9A-Za-z]{10})",
    re.IGNORECASE,
)

API_VIEW = "https://api.bilibili.com/x/web-interface/view"

# 防刷：同一会话（群/私聊）N 秒内只解析一次
RATE_LIMIT_SECONDS = 30
_last_handle: Dict[str, float] = {}

matcher = on_message(priority=10, block=False)


def _extract_bvid(event: MessageEvent) -> Optional[str]:
    """从消息中提取 BV 号或短链，兼容纯文本与 JSON 小程序卡片。"""
    text = event.get_plaintext()
    m = BILI_LINK_RE.search(text) or BVID_RE.search(text)
    if m:
        return m.group(1)
    m = B23_RE.search(text)
    if m:
        return m.group(0)  # 短链，稍后展开
    # QQ 里 B 站分享通常是小程序卡片（OneBot 的 json 消息段）
    for seg in event.message:
        if seg.type == "json":
            try:
                data = json.loads(seg.data.get("data") or "{}")
                raw = json.dumps(data, ensure_ascii=False)
                m = BILI_LINK_RE.search(raw) or B23_RE.search(raw) or BVID_RE.search(raw)
                if m:
                    return m.group(1) if m.group(1).startswith("BV") else m.group(0)
            except Exception:
                continue
    return None


async def _resolve_bvid(raw: str) -> Optional[str]:
    """b23.tv 短链展开为真实 BV 号。"""
    if raw.startswith("http"):
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=10) as client:
                resp = await client.get(raw)
                m = BVID_RE.search(str(resp.url))
                if m:
                    return m.group(1)
        except Exception:
            return None
    return raw


async def _fetch_video(bvid: str) -> Optional[Dict]:
    """调用 B站公开接口获取视频信息。"""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(API_VIEW, params={"bvid": bvid})
            payload = resp.json()
            if payload.get("code") != 0:
                return None
            return payload.get("data")
    except Exception:
        return None


def _fmt_duration(seconds: int) -> str:
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _fmt_count(n: int) -> str:
    if n >= 100_000_000:
        return f"{n / 100_000_000:.1f}亿"
    if n >= 10_000:
        return f"{n / 10_000:.1f}万"
    return str(n)


@matcher.handle()
async def handle(bot: Bot, event: MessageEvent):
    now = time.time()
    session = getattr(event, "group_id", None) or event.user_id
    if now - _last_handle.get(session, 0) < RATE_LIMIT_SECONDS:
        return

    raw = _extract_bvid(event)
    if not raw:
        return
    bvid = await _resolve_bvid(raw)
    if not bvid or not bvid.startswith("BV"):
        return
    info = await _fetch_video(bvid)
    if not info:
        return
    _last_handle[session] = now

    title = info.get("title", "未知标题")
    owner = (info.get("owner") or {}).get("name", "未知UP主")
    stat = info.get("stat") or {}
    duration = _fmt_duration(info.get("duration", 0))
    pic = info.get("pic", "")
    url = f"https://www.bilibili.com/video/{bvid}"

    text = (
        f"【B站视频】\n"
        f"标题：{title}\n"
        f"UP主：{owner}\n"
        f"时长：{duration} ｜ 播放：{_fmt_count(stat.get('view', 0))} "
        f"｜ 点赞：{_fmt_count(stat.get('like', 0))}\n"
        f"{url}"
    )

    # 优先带封面图发送；图片失败时退化为纯文字
    try:
        segs = [MessageSegment.text(text)]
        if pic:
            segs.append(MessageSegment.image(pic))
        await bot.send(event, segs)
    except Exception:
        try:
            await bot.send(event, MessageSegment.text(text))
        except Exception:
            pass
