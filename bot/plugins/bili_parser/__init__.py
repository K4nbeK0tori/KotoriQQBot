"""B站视频解析插件：监听群消息中的哔哩哔哩链接，解析并回复视频信息。"""

import json
import time
from typing import Dict

from nonebot import logger, on_message
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, MessageSegment

from .core import (
    build_reply,
    download_image_base64,
    extract_bvid,
    fetch_video,
    resolve_bvid,
)

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
        logger.info(f"[bili] 限流跳过: session={session}")
        return

    raw = extract_bvid(event.get_plaintext(), _collect_json_payloads(event))
    logger.info(f"[bili] 提取: {raw!r}")
    if not raw:
        return
    bvid = await resolve_bvid(raw)
    logger.info(f"[bili] 展开: {bvid!r}")
    if not bvid or not bvid.startswith("BV"):
        return
    info = await fetch_video(bvid)
    if not info:
        logger.warning(f"[bili] B站API查询失败: {bvid}")
        return
    logger.info(f"[bili] API成功: {bvid} title={info.get('title', '')[:24]}")
    _last_handle[session] = now

    text = build_reply(info, bvid)
    pic = info.get("pic", "")

    # 方式1：文字 + 封面图（下载转 base64，绕过 B站防盗链）
    try:
        segs = [MessageSegment.text(text)]
        if pic:
            img = await download_image_base64(pic)
            if img:
                segs.append(MessageSegment.image(img))
            else:
                logger.warning(f"[bili] 封面图下载失败，仅发文字: {pic}")
        await bot.send(event, segs)
        logger.info(f"[bili] 发送成功: {bvid} -> {session}")
        return
    except Exception as e:
        logger.warning(f"[bili] 带图发送失败: {e!r}")

    # 方式2：降级纯文字
    try:
        await bot.send(event, MessageSegment.text(text))
        logger.info(f"[bili] 发送成功(纯文字): {bvid} -> {session}")
    except Exception as e:
        logger.error(f"[bili] 发送失败: {bvid} -> {e!r}")
