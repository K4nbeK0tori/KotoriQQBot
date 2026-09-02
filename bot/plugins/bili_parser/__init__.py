"""B站视频解析插件：监听群消息中的哔哩哔哩链接。

流程：提取 BV 号 → 查视频信息 → 下载视频 → 发送「解析信息 + 视频」→ 删除本地临时文件。
下载或发送失败时，至少发送「视频信息 + 封面图」。
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
RATE_LIMIT_SECONDS = 60
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


FAIL_MSG = "哎呀，老师的视频剪辑失败了呢，需要我给你讲个小故事吗，比如“人类与火的故事？”"


async def _recall(bot: Bot, msg_id) -> bool:
    """撤回一条消息（机器人自己发的）。msg_id 为空则跳过。"""
    if not msg_id:
        return False
    try:
        await bot.call_api("delete_msg", message_id=msg_id)
        logger.info("[bili] 已撤回剪辑提示")
        return True
    except Exception as e:
        logger.warning(f"[bili] 撤回剪辑提示失败: {e!r}")
        return False


async def _send_fail(bot: Bot, event: MessageEvent, bvid: str, session) -> bool:
    """发送剪辑失败提示。返回是否成功。"""
    try:
        await bot.send(event, MessageSegment.text(FAIL_MSG))
        logger.info(f"[bili] 失败提示已发送: {bvid} -> {session}")
        return True
    except Exception as e:
        logger.warning(f"[bili] 失败提示发送失败: {bvid} -> {e!r}")
        return False


async def _send_forward(
    bot: Bot, event: MessageEvent, session, text: str, info, mp4_path: str
) -> bool:
    """把信息卡 + 视频整合为一条合并转发（聊天记录）发送。返回是否成功。

    节点只用文字 + 视频（封面图节点易导致合并转发失败，已移除）；
    失败自动重试一次。
    """
    my_id = str(bot.self_id)
    nodes = [
        MessageSegment.node_custom(my_id, "薇欧拉", MessageSegment.text(text)),
        MessageSegment.node_custom(my_id, "薇欧拉", MessageSegment.video(file=mp4_path)),
    ]
    for attempt in range(2):
        try:
            if hasattr(event, "group_id"):
                await bot.call_api(
                    "send_group_forward_msg", group_id=session, messages=nodes
                )
            else:
                await bot.call_api(
                    "send_private_forward_msg", user_id=session, messages=nodes
                )
            logger.info(
                f"[bili] 合并转发已发送: {os.path.basename(mp4_path)} -> {session}"
            )
            return True
        except Exception as e:
            logger.warning(f"[bili] 合并转发失败({attempt + 1}/2): {e!r}")
            await asyncio.sleep(1.5)
    return False


async def _send_info(
    bot: Bot, event: MessageEvent, text: str, info, bvid: str, session
) -> bool:
    """发送文字解析信息 + 封面图。返回是否成功。"""
    try:
        segs = [MessageSegment.text(text)]
        pic = info.get("pic", "")
        if pic:
            img = await download_image_base64(pic)
            if img:
                segs.append(MessageSegment.image(img))
        await bot.send(event, segs)
        logger.info(f"[bili] 解析信息已发送: {bvid} -> {session}")
        return True
    except Exception as e:
        logger.error(f"[bili] 解析信息发送失败: {bvid} -> {e!r}")
        return False


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

    # 视频时长限制：超过 15 分钟不下载，直接失败提示
    MAX_DURATION_SECONDS = 15 * 60
    duration = info.get("duration", 0)
    if duration > MAX_DURATION_SECONDS:
        logger.info(f"[bili] 视频超长跳过: {bvid} duration={duration}s")
        await _send_fail(bot, event, bvid, session)
        return

    _last_handle[session] = now

    title = info.get("title", bvid)
    cid = info.get("cid")
    text = build_reply(info, bvid)

    # 下载提示（记录消息 ID，视频发出后撤回）
    tip_msg_id = None
    try:
        tip_msg_id = await bot.send(
            event,
            MessageSegment.text(f"已经收到老师发送的（{bvid}），薇欧拉酱正在善意剪辑哦~"),
        )
    except Exception as e:
        logger.warning(f"[bili] 下载提示发送失败: {e!r}")

    # 主流程：下载视频 → 合并转发 → 删除
    async with _download_lock:
        logger.info(f"[bili] 开始下载: {bvid}")
        mp4, err = await download_video(bvid, title, cid)
        if mp4:
            logger.info(f"[bili] 下载完成: {mp4}")
            # 只发合并转发（聊天记录）；失败不降级，直接发失败提示
            sent = await _send_forward(bot, event, session, text, info, mp4)
            try:
                os.remove(mp4)
                logger.info(f"[bili] 已删除临时文件: {os.path.basename(mp4)}")
            except OSError:
                pass
            if sent:
                await _recall(bot, tip_msg_id)  # 视频发出，撤回剪辑提示
                logger.info(f"[bili] 视频已发送: {bvid} -> {session}")
            else:
                await _send_fail(bot, event, bvid, session)
                logger.warning(f"[bili] 合并转发失败，已发失败提示: {bvid}")
            return
        logger.warning(f"[bili] 下载失败: {bvid} ({err})")

    # 下载失败：失败消息
    await _send_fail(bot, event, bvid, session)
