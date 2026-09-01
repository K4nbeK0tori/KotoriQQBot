"""本地演示脚本：无需 Docker / NoneBot，模拟群消息跑通 B站解析逻辑。

用法：
    python demo.py                    # 跑内置样例
    python demo.py <链接或BV号>       # 测试你自己的链接（支持 b23.tv 短链）
"""

import asyncio
import importlib.util
import json
import sys

# 直接按文件路径加载 core 模块，避免触发包 __init__.py（其依赖 nonebot）
def _load_core():
    spec = importlib.util.spec_from_file_location(
        "bili_core", "bot/plugins/bili_parser/core.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


core = _load_core()
build_reply = core.build_reply
extract_bvid = core.extract_bvid
fetch_video = core.fetch_video
resolve_bvid = core.resolve_bvid

# QQ 小程序卡片模拟（实际是 OneBot json 段里的 data 字段）
CARD_PAYLOAD = json.dumps(
    {
        "app": "com.tencent.mini.video",
        "meta": {
            "detail_1": {
                "qqdocurl": "https://www.bilibili.com/video/BV1xx411c7mD",
                "title": "示例卡片",
            }
        },
    },
    ensure_ascii=False,
)

SAMPLES = [
    ("① 完整视频链接", "https://www.bilibili.com/video/BV1xx411c7mD?p=1&share_source=copy_web", None),
    ("② 纯 BV 号", "这个视频好棒 BV1E7411w7wC 推荐一下", None),
    ("③ b23.tv 短链", "https://b23.tv/BV1Q541167Qg", None),  # 占位短链，若解析失败属正常
    ("④ QQ 小程序卡片", "", [CARD_PAYLOAD]),
]


async def run_case(label: str, text: str, payloads):
    print(f"\n{'=' * 56}\n{label}\n  消息: {text or '(小程序卡片)'}")
    raw = extract_bvid(text, payloads)
    if not raw:
        print("  [提取] 未找到 B站链接，跳过")
        return
    print(f"  [提取] -> {raw}")
    bvid = await resolve_bvid(raw)
    if not bvid or not bvid.startswith("BV"):
        print("  [展开] 未能解析出 BV 号（短链可能失效）")
        return
    print(f"  [展开] -> {bvid}")
    info = await fetch_video(bvid)
    if not info:
        print("  [API] 查询失败（视频不存在或接口风控）")
        return
    print("  [API] 查询成功，封面图: " + (info.get("pic") or "(无)"))
    print("-" * 56)
    print(build_reply(info, bvid))


async def main():
    args = sys.argv[1:]
    if args:
        await run_case("自定义测试", args[0], None)
    else:
        for label, text, payloads in SAMPLES:
            await run_case(label, text, payloads)
    print(f"\n{'=' * 56}\n演示结束：以上即为机器人在群里回复的内容格式喵。")


if __name__ == "__main__":
    asyncio.run(main())
