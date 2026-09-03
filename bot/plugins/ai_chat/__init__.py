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
import urllib.parse
import urllib.request
from typing import Dict, List, Optional

from nonebot import logger, on_command, on_message
from nonebot import get_asgi
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
CHAT_COOLDOWN = 5  # 聊天冷却（秒）
MAX_CONTENT = 500  # 单条输入最大长度

TRIGGER_WORDS = ("薇欧拉", "薇欧拉酱", "viola")

# 每群上下文
_contexts: Dict[str, List[dict]] = {}
_last_chat: Dict[str, float] = {}

# ===== 数据读写 =====

USAGE_FILE = os.path.join(DATA_DIR, "usage.json")


def _record_usage(prompt_tokens: int, completion_tokens: int):
    """按天累计 DeepSeek token 用量（写入 /data/usage.json，面板图表用）。"""
    try:
        data = {}
        try:
            with open(USAGE_FILE, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            pass
        day = time.strftime("%Y-%m-%d")
        d = data.setdefault(day, {"prompt": 0, "completion": 0})
        d["prompt"] += int(prompt_tokens or 0)
        d["completion"] += int(completion_tokens or 0)
        tmp = USAGE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, USAGE_FILE)
    except Exception as e:
        logger.warning(f"[ai] 记录 usage 失败: {e!r}")


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


def _admin_role_name() -> Optional[str]:
    """超级管理员专属角色卡名（面板配置），未设置返回 None。"""
    data = _load_admin_data()
    name = data.get("admin_role")
    if name and _load_role(name):
        return name
    return None


def _web_search_enabled() -> bool:
    """联网搜索增强开关（面板配置）。"""
    data = _load_admin_data()
    return bool(data.get("web_search", False))


def _search_web(query: str, top: int = 5) -> List[str]:
    """用 Bing RSS 搜索实时信息，返回标题/摘要/链接列表。"""
    try:
        url = "https://www.bing.com/search?q=" + urllib.parse.quote(query) + "&format=rss"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0"})
        xml = urllib.request.urlopen(req, timeout=12).read().decode("utf-8", "replace")
        items = re.findall(
            r"<item>.*?<title>(.*?)</title>.*?<link>(.*?)</link>.*?<description>(.*?)</description>",
            xml,
            re.DOTALL,
        )
        results = []
        for t, l, d in items[:top]:
            t = re.sub(r"<[^>]+>", "", t).strip()
            d = re.sub(r"<[^>]+>", "", d).strip()
            if t:
                results.append(f"{t}：{d[:150]}（{l}）")
        return results
    except Exception as e:
        logger.warning(f"[ai] 联网搜索失败: {e!r}")
        return []


# ===== DeepSeek =====


async def _chat(system: str, history: List[dict]) -> tuple:
    """调用 DeepSeek，返回 (回复内容, usage 字典)。失败返回 (None, {})。"""
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
        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage") or {}
        return content, usage
    except Exception as e:
        logger.error(f"[ai] DeepSeek 调用失败: {e!r}")
        return None, {}


async def _do_chat(
    bot: Bot,
    event: MessageEvent,
    content: str,
    gid: str,
    role_override: Optional[str] = None,
    uid: str = "",
):
    # 只用角色卡（无内置提示词）；没配角色卡时 system 为空
    name = role_override or _group_role(gid)
    role = _load_role(name)
    system = (role or {}).get("system", "")

    # 注入当前真实时间（DeepSeek 知识截止较早，避免日期/时间问题胡答）
    now_str = time.strftime("%Y年%m月%d日 %H:%M")
    time_note = f"当前真实时间是：{now_str}（回答日期、时间、今天之类的问题时以这个为准，不要使用你的知识截止日期）"
    system = (time_note + "\n\n" + system) if system else time_note

    # 联网搜索增强：开启时先搜索，把实时结果注入 system
    if _web_search_enabled():
        results = await asyncio.to_thread(_search_web, content)
        logger.info(f"[ai] 联网搜索结果 {len(results)} 条")
        if results:
            note = "\n\n[以下为联网搜索到的实时信息，可据此回答；若与问题无关就忽略]\n" + "\n".join(results)
            system = (system + note) if system else note

    # 上下文按 群+用户 隔离，避免不同人设之间串扰
    key = f"{gid}:{uid}" if uid else gid
    history = _contexts.setdefault(key, [])
    history.append({"role": "user", "content": content})
    history = history[-MAX_HISTORY:]

    logger.info(f"[ai] 调用 DeepSeek: system_len={len(system)} history={len(history)} key={bool(DEEPSEEK_API_KEY)}")

    reply, usage = await _chat(system, history)
    if reply:
        _record_usage(usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))
        history.append({"role": "assistant", "content": reply})
        _contexts[key] = history[-MAX_HISTORY:]
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
    await _do_chat(
        bot, event, content[:MAX_CONTENT], gid, None, str(event.user_id)
    )


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
        role = _load_role(name)
        if not role:
            await bot.send(event, f"本群角色卡「{name}」不存在，请在管理面板创建喵")
            return
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
    text = event.get_plaintext().strip()

    # 调试日志：打印消息段结构，确认 at 段实际内容
    segs_desc = "; ".join(f"{s.type}:{json.dumps(s.data, ensure_ascii=False)}" for s in event.message)
    raw_msg = getattr(event, "raw_message", "") or str(event.message)
    logger.info(f"[ai] 收到消息 text={text!r} raw={raw_msg[:150]!r} segments=[{segs_desc[:150]}]")

    # 触发判断：@机器人（优先用 raw_message 的 CQ 码，兼容段丢失；to_me 兜底）
    at_me = False
    for q in re.findall(r"\[CQ:at,qq=(\d+)\]", raw_msg):
        if q in (str(bot.self_id), str(event.self_id)):
            at_me = True
    if not at_me:
        at_me = bool(getattr(event, "to_me", False))
    logger.info(f"[ai] at_me={at_me} to_me={getattr(event, 'to_me', None)} self_id={bot.self_id}")

    # 群开关：未开启时完全静默（@、触发词、纯@ 都不回）
    if not _group_enabled(gid):
        logger.info(f"[ai] 群 {gid} 未开启AI，静默跳过")
        return

    if at_me and not text:
        # 纯 @ 没带话（群已开启）
        try:
            await bot.send(event, "喊妈妈什么事喵？")
        except Exception:
            pass
        return

    content = None
    if at_me:
        content = text  # plaintext 不含 at 段，剩下的就是对话内容
    else:
        if not text:
            return
        # 触发判断：文字含触发词
        for w in TRIGGER_WORDS:
            if w in text:
                content = re.sub(
                    rf"^{w}[ \t]*[,，:：]?[ \t]*", "", text, count=1
                ).strip()
                break
    if not content:
        return

    now = time.time()
    if now - _last_chat.get(gid, 0) < CHAT_COOLDOWN:
        logger.info(f"[ai] 群 {gid} 冷却中，跳过")
        return
    _last_chat[gid] = now
    logger.info(f"[ai] 进入对话: {gid} content={content[:50]!r}")

    # 超管 @ 时使用超管专属角色卡（面板配置）
    role_override = None
    if at_me and _is_superadmin(event.user_id):
        role_override = _admin_role_name()
    await _do_chat(
        bot, event, content[:MAX_CONTENT], gid, role_override, str(event.user_id)
    )


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


# ===== 清除上下文：群命令 + HTTP 接口（面板按钮调用） =====

clear_cmd = on_command("clear", aliases={"忘记", "清空记忆"}, priority=5, block=True)


@clear_cmd.handle()
async def _clear_cmd(bot: Bot, event: MessageEvent):
    if not isinstance(event, GroupMessageEvent):
        return
    gid = str(event.group_id)
    uid = str(event.user_id)
    arg = event.get_plaintext().replace("/clear", "", 1).strip().lower()
    if arg in ("all", "全部") and _is_superadmin(event.user_id):
        keys = [k for k in _contexts if k.startswith(gid + ":") or k == gid]
        for k in keys:
            _contexts.pop(k, None)
        await bot.send(event, f"已清除本群全部记忆（{len(keys)} 个会话）喵")
    else:
        _contexts.pop(f"{gid}:{uid}", None)
        await bot.send(event, "我已经把和你的对话记忆清空啦喵~")


try:
    _asgi = get_asgi()

    @_asgi.post("/api/ai/clear")
    async def _api_ai_clear(body: dict):
        gid = str((body or {}).get("gid", ""))
        if gid:
            keys = [k for k in _contexts if k.startswith(gid + ":") or k == gid]
        else:
            keys = list(_contexts.keys())
        for k in keys:
            _contexts.pop(k, None)
        logger.info(f"[ai] HTTP 清除上下文: gid={gid or 'all'} 共 {len(keys)} 个会话")
        return {"ok": True, "cleared": len(keys)}

except Exception as e:
    logger.warning(f"[ai] 注册 HTTP 清除接口失败: {e!r}")
