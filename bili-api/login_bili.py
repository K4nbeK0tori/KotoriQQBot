"""B站登录脚本（容器内扫码登录，生成 cookies.txt）。

用法（在 bili-api 容器内）：
    docker exec -it qqbot-bili-api python /app/login_bili.py

流程：打印二维码 URL → 手机 B站 APP 扫码确认 → 自动轮询 → 写入 /app/cookies.txt
（cookies.txt 挂载自宿主机 ./bili-api/cookies.txt，登录态持久化）。

注意：扫码后请耐心等待脚本自行轮询完成（每 5 秒一次，最长 5 分钟），
看到 "登录成功! cookies 已写入 /app/cookies.txt" 再操作。
"""

import requests
import time

s = requests.Session()
s.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
})

r = s.get("https://passport.bilibili.com/x/passport-login/web/qrcode/generate", timeout=10).json()
key = r["data"]["qrcode_key"]
url = r["data"]["url"]
print("=== 复制下面的 URL 到 https://cli.im 生成二维码，用手机B站APP扫码 ===")
print(url)
print("=====================================================================")

for i in range(60):
    time.sleep(5)
    d = s.get(
        "https://passport.bilibili.com/x/passport-login/web/qrcode/poll",
        params={"qrcode_key": key},
        timeout=10,
    ).json()
    code = d["data"]["code"]
    if code == 0:
        cookies = "; ".join(f"{c.name}={c.value}" for c in s.cookies)
        with open("/app/cookies.txt", "w", encoding="utf-8") as f:
            f.write(cookies)
        print("登录成功! cookies 已写入 /app/cookies.txt")
        print(cookies[:100])
        break
    elif code == 86038:
        print("二维码已过期")
        break
    elif code == 86090:
        print("已扫码，请在手机上确认登录")
    else:
        print(f"等待扫码... ({i + 1}/60)")
else:
    print("登录超时")
