"""AI 对话插件：DeepSeek 角色扮演聊天（梦限大·薇欧拉）。

功能：
- 触发：消息文本含「薇欧拉」或 @机器人
- 群开关：每群独立开关（默认关闭，超级管理员可开）
- 角色卡：/data/role_cards/<name>.json，每群可指定不同卡
- 超级管理员：/data/admin_data.json 的 superadmins 列表
- 命令：/help /chat /ai on|off /role [名字]

配置数据（/data 共享卷，与 admin-web 共用）：
- /data/admin_data.json   {superadmins, groups:{群号:{enabled,role}}, default_role}
- /data/role_cards/*.json {name, description, system}
"""

import asyncio
import json
import os
import re
import time
import urllib.request
from typing import Dict, List, Optional

from nonebot import logger, on_command, on_message
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    MessageEvent,
    MessageSegment,
)

# ===== 配置 =====
DATA_DIR = os.environ.get("AI_DATA_DIR", "/data")
ADMIN_DATA = os.path.join(DATA_DIR, "admin_data.json")
ROLE_DIR = os.path.join(DATA_DIR, "role_cards")

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_URL = os.environ.get("DEEPSEEK_URL", "https://api.deepseek.com/v1/chat/completions")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")

MAX_HISTORY = 20   # 每群保留的最近轮数
CHAT_COOLDOWN = 15  # 群内对话冷却（秒）
MAX_CONTENT = 500  # 单条输入最大长度

TRIGGER_WORDS = ("薇欧拉", "薇欧拉酱", "viola")

# 内置默认角色（role_cards 目录不存在或没有该卡时兜底）
DEFAULT_ROLE = {
    "name": "薇欧拉",
    "description": "来自梦限大的猫娘女仆机器人，温柔俏皮，说话带喵。",
    "system": (
        "你是薇欧拉，来自梦限大的猫娘女仆机器人。"
        "性格温柔、俏皮，带一点小傲娇，说话喜欢在句尾带\"喵\"。"
        "你称呼用户为\"老师\"，自称\"薇欧拉\"。"
        "你在QQ群里帮老师们解决问题，会认真回答，也会开玩笑。"
        "回复保持简洁自然，一般2到4句话，不要长篇大论，不要使用markdown。"
        "根据对话内容自然回应，始终维持猫娘女仆的角色设定。"
    ),
}

# 每群上下文
_contexts: Dict[str, List[dict]] = {}
_last_chat: Dict[str, float] = {}

# ===== 数据读写 =====


def _load_admin_data() -> dict:
    try:
        with open(ADMIN_DATA, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"superadmins": [], "groups": {}, "default_role": "viola"}


def _save_admin_data(data: dict) -> bool:
    try:
        tmp = ADMIN_DATA + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, ADMIN_DATA)
        return True
    except Exception as e:
        logger.error(f"[ai] 保存 admin_data 失败: {e!r}")
        return False


def _load_role(name: str) -> Optional[dict]:
    path = os.path.join(ROLE_DIR, f"{name}.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _group_enabled(gid) -> bool:
    data = _load_admin_data()
    return bool((data.get("groups") or {}).get(str(gid), {}).get("enabled", False))


def _group_role(gid) -> str:
    data = _load_admin_data()
    return (
        (data.get("groups") or {}).get(str(gid), {}).get("role")
        or data.get("default_role", "viola")
    )


def _is_superadmin(qq) -> bool:
    data = _load_admin_data()
    return str(qq) in {str(x) for x in data.get("superadmins", [])}


# ===== DeepSeek =====


async def _chat(system: str, history: List[dict]) -> Optional[str]:
    if not DEEPSEEK_API_KEY:
        return None
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [{"role": "system", "content": system}] + history,
        "temperature": 0.8,
    }

    def _do():
        req = urllib.request.Request(
            DEEPSEEK_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=90) as resp:
            return json.loads(resp.read().decode("utf-8"))

    try:
        data = await asyncio.wait_for(asyncio.to_thread(_do), timeout=100)
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error(f"[ai] DeepSeek 调用失败: {e!r}")
        return None


async def _do_chat(bot: Bot, event: MessageEvent, content: str, gid: str):
    role = _load_role(_group_role(gid)) or DEFAULT_ROLE
    system = role.get("system") or DEFAULT_ROLE["system"]

    history = _contexts.setdefault(gid, [])
    history.append({"role": "user", "content": content})
    history = history[-MAX_HISTORY:]

    reply = await _chat(system, history)
    if reply:
        history.append({"role": "assistant", "content": reply})
        _contexts[gid] = history[-MAX_HISTORY:]
        try:
            await bot.send(event, MessageSegment.text(reply))
        except Exception as e:
            logger.warning(f"[ai] 回复发送失败: {e!r}")
    else:
        try:
            await bot.send(event, "啊嘞，薇欧拉的大脑走神了，请稍后再喊我喵~")
        except Exception:
            pass


# ===== 命令 =====

MENU = (
    "🌸 薇欧拉菜单：\n"
    "/help - 本菜单\n"
    "/chat <内容> - 和薇欧拉聊天\n"
    "@薇欧拉 <内容> 或 消息里带「薇欧拉」- 直接聊\n"
    "/role - 查看当前角色卡\n"
    "/role <名字> - 切换本群角色卡（管理员）\n"
    "/ai on|off - 开启/关闭本群AI（管理员）\n"
    "\n💡 本群AI默认关闭，需管理员用 /ai on 开启喵"
)

help_cmd = on_command("help", aliases={"菜单"}, priority=5, block=True)


@help_cmd.handle()
async def _help(bot: Bot, event: MessageEvent):
    await bot.send(event, MessageSegment.text(MENU))


chat_cmd = on_command("chat", priority=5, block=True)


@chat_cmd.handle()
async def _chat_cmd(bot: Bot, event: MessageEvent):
    if not isinstance(event, GroupMessageEvent):
        return
    if not _group_enabled(event.group_id):
        await bot.send(event, "本群AI还没开启喵，让管理员发 /ai on 开启一下哦")
        return
    content = event.get_plaintext().replace("/chat", "", 1).strip()
    if not content:
        await bot.send(event, "想聊什么呢，老师喵？在 /chat 后面加上你想说的就行")
        return
    gid = str(event.group_id)
    now = time.time()
    if now - _last_chat.get(gid, 0) < CHAT_COOLDOWN:
        return
    _last_chat[gid] = now
    await _do_chat(bot, event, content[:MAX_CONTENT], gid)


ai_cmd = on_command("ai", priority=5, block=True)


@ai_cmd.handle()
async def _ai_cmd(bot: Bot, event: MessageEvent):
    if not isinstance(event, GroupMessageEvent):
        return
    if not _is_superadmin(event.user_id):
        await bot.send(event, "这个命令只有超级管理员才能用喵~")
        return
    arg = event.get_plaintext().replace("/ai", "", 1).strip().lower()
    data = _load_admin_data()
    groups = data.setdefault("groups", {})
    g = groups.setdefault(str(event.group_id), {})
    if arg in ("on", "开", "开启"):
        g["enabled"] = True
    elif arg in ("off", "关", "关闭"):
        g["enabled"] = False
    else:
        await bot.send(event, "用法：/ai on 或 /ai off 喵")
        return
    if _save_admin_data(data):
        await bot.send(event, f"本群AI已{'开启' if g['enabled'] else '关闭'}喵~")
    else:
        await bot.send(event, "保存配置失败了喵，看看日志吧")


role_cmd = on_command("role", priority=5, block=True)


@role_cmd.handle()
async def _role_cmd(bot: Bot, event: MessageEvent):
    if not isinstance(event, GroupMessageEvent):
        return
    arg = event.get_plaintext().replace("/role", "", 1).strip()
    gid = str(event.group_id)
    if not arg:
        name = _group_role(gid)
        role = _load_role(name) or DEFAULT_ROLE
        await bot.send(
            event,
            MessageSegment.text(
                f"当前角色：{role.get('name', name)}\n"
                f"简介：{role.get('description', '（无简介）')}"
            ),
        )
        return
    # 切换角色（管理员）
    if not _is_superadmin(event.user_id):
        await bot.send(event, "这个命令只有超级管理员才能用喵~")
        return
    name = arg.strip()
    role = _load_role(name)
    if not role:
        await bot.send(event, f"找不到角色卡「{name}」喵，可以在管理面板里添加")
        return
    data = _load_admin_data()
    data.setdefault("groups", {}).setdefault(gid, {})["role"] = name
    if _save_admin_data(data):
        await bot.send(event, f"本群角色已切换为「{role.get('name', name)}」喵~")
    else:
        await bot.send(event, "保存配置失败了喵，看看日志吧")


# ===== 消息触发 =====

chat_matcher = on_message(priority=20, block=False)

@chat_matcher.handle()
async def _msg_chat(bot: Bot, event: MessageEvent):
    if not isinstance(event, GroupMessageEvent):
        return
    gid = str(event.group_id)
    if not _group_enabled(gid):
        return
    text = event.get_plaintext().strip()
    if not text:
        return

    # 触发判断：@机器人
    content = None
    for seg in event.message:
        if seg.type == "at" and str(seg.data.get("qq")) == str(bot.self_id):
            content = re.sub(r"\[CQ:at[^\]]*\]", "", text).strip()
            break
    # 触发判断：文字含触发词
    if content is None:
        for w in TRIGGER_WORDS:
            if w in text:
                content = re.sub(
                    rf"^(?:{w}[ \t]*[,，:：]?[ \t]*|.*?{w}[ \t]*[,，:：]?[ \t]*)",
                    "",
                    text,
                    count=1,
                ).strip()
                break
    if not content:
        return

    now = time.time()
    if now - _last_chat.get(gid, 0) < CHAT_COOLDOWN:
        return
    _last_chat[gid] = now
    await _do_chat(bot, event, content[:MAX_CONTENT], gid)


# ===== 初始化：SUPERADMINS 环境变量作为初始超级管理员种子 =====

def _seed_superadmins():
    seed = os.environ.get("SUPERADMINS", "")
    admins = [x.strip() for x in seed.split(",") if x.strip()]
    if not admins:
        return
    data = _load_admin_data()
    if not data.get("superadmins"):
        data["superadmins"] = admins
        _save_admin_data(data)
        logger.info(f"[ai] 已初始化超级管理员: {admins}")


_seed_superadmins()
