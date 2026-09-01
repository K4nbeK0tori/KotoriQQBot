"""B站视频解析插件：监听群消息中的哔哩哔哩链接。

流程：提取 BV 号 → 查视频信息 → 下载视频(480p) → 发送群文件 → 删除本地临时文件。
下载或发送失败时，降级为发送「视频信息 + 封面图」。
"""

import asyncio
import json
import os
import time
from typing import Dict

from nonebot import logger, on_message
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, MessageSegment

from .core import (
    build_reply,
    download_image_base64,
    download_video,
    extract_bvid,
    fetch_video,
    resolve_bvid,
)

# 防刷：下载是重操作，同一会话 N 秒内只处理一次
RATE_LIMIT_SECONDS = 120
_last_handle: Dict[str, float] = {}
# 全局下载锁：1C1G 机器上同时只允许一个下载，防止资源耗尽
_download_lock = asyncio.Lock()

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


async def _send_video(bot: Bot, event: MessageEvent, session, mp4_path: str) -> str:
    """发送视频：优先发视频消息（聊天内直接播放），失败降级发群文件。

    返回 "video" / "file" / "fail"。
    """
    # 1. 视频消息（聊天内显示视频卡片）
    try:
        await bot.send(event, MessageSegment.video(file=mp4_path))
        return "video"
    except Exception as e:
        logger.warning(f"[bili] 视频消息发送失败: {e!r}，降级发群文件")

    # 2. 群文件（兜底，大小限制更宽松）
    name = os.path.basename(mp4_path)
    try:
        if hasattr(event, "group_id"):
            await bot.call_api(
                "upload_group_file", group_id=session, file=mp4_path, name=name
            )
        else:
            await bot.call_api(
                "upload_private_file", user_id=session, file=mp4_path, name=name
            )
        return "file"
    except Exception as e:
        logger.warning(f"[bili] 群文件也失败: {e!r}")
        return "fail"


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

    title = info.get("title", bvid)
    cid = info.get("cid")
    text = build_reply(info, bvid)

    # 主流程：下载视频 → 发文件 → 删除
    async with _download_lock:
        logger.info(f"[bili] 开始下载: {bvid}")
        mp4, err = await download_video(bvid, title, cid)
        if mp4:
            logger.info(f"[bili] 下载完成: {mp4}")
            sent = await _send_video(bot, event, session, mp4)
            try:
                os.remove(mp4)
                logger.info(f"[bili] 已删除临时文件: {os.path.basename(mp4)}")
            except OSError:
                pass
            if sent != "fail":
                logger.info(f"[bili] 视频已发送({sent}): {bvid} -> {session}")
                return
            logger.warning(f"[bili] 发送失败，降级发解析信息: {bvid}")
        else:
            logger.warning(f"[bili] 下载失败: {bvid} ({err})")

    # 降级：发送视频信息 + 封面图
    try:
        segs = [MessageSegment.text(text)]
        pic = info.get("pic", "")
        if pic:
            img = await download_image_base64(pic)
            if img:
                segs.append(MessageSegment.image(img))
        await bot.send(event, segs)
        logger.info(f"[bili] 解析信息已发送: {bvid} -> {session}")
    except Exception as e:
        logger.error(f"[bili] 降级发送失败: {bvid} -> {e!r}")
