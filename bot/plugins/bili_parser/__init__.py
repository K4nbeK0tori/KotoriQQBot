"""B站视频解析插件：监听群消息中的哔哩哔哩链接，解析并回复视频信息。"""

import json
import time
from typing import Dict

from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, MessageSegment

from .core import build_reply, extract_bvid, fetch_video, resolve_bvid

# 防刷：同一会话（群/私聊）N 秒内只解析一次
RATE_LIMIT_SECONDS = 30
_last_handle: Dict[str, float] = {}

matcher = on_message(priority=10, block=False)


def _collect_json_payloads(event: MessageEvent):
    """把消息里的 JSON 段（QQ 小程序卡片）转成原始 JSON 字符串列表。"""
    payloads = []
    for seg in event.message:
        if seg.type == "json":
            try:
                data = json.loads(seg.data.get("data") or "{}")
                payloads.append(json.dumps(data, ensure_ascii=False))
            except Exception:
                continue
    return payloads


@matcher.handle()
async def handle(bot: Bot, event: MessageEvent):
    now = time.time()
    session = getattr(event, "group_id", None) or event.user_id
    if now - _last_handle.get(session, 0) < RATE_LIMIT_SECONDS:
        return

    raw = extract_bvid(event.get_plaintext(), _collect_json_payloads(event))
    if not raw:
        return
    bvid = await resolve_bvid(raw)
    if not bvid or not bvid.startswith("BV"):
        return
    info = await fetch_video(bvid)
    if not info:
        return
    _last_handle[session] = now

    text = build_reply(info, bvid)
    pic = info.get("pic", "")
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
