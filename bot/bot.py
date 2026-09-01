"""NoneBot2 入口：初始化框架，注册 OneBot V11 适配器，加载 B站解析插件。"""
import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter

nonebot.init(
    driver="~fastapi+~httpx+~websockets",
    host="0.0.0.0",
    port=8080,  # bot 自身不对外映射端口，这里只是框架内部监听
)

driver = nonebot.get_driver()
driver.register_adapter(OneBotV11Adapter)

nonebot.load_plugin("plugins.bili_parser")
nonebot.load_plugin("plugins.ai_chat")

if __name__ == "__main__":
    nonebot.run()
