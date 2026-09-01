"""B站登录 Web 管理页：浏览器获取二维码 → 扫码 → 自动写入 cookies.txt。

用法（容器内）：
    python app.py          # 监听 0.0.0.0:8123

页面：/       扫码登录页（获取二维码、轮询状态）
API：/api/qrcode  生成二维码（base64）
    /api/poll    轮询登录状态；成功时将 session cookie 写入 COOKIE_FILE
"""

import base64
import io
import time

import qrcode
import requests
from flask import Flask, Response, jsonify

app = Flask(__name__)

COOKIE_FILE = "/data/cookies.txt"
GENERATE_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
POLL_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

STATE: dict = {"session": None, "key": None, "ts": 0.0}

HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>B站登录管理</title>
<style>
  body { font-family: system-ui, sans-serif; background:#0f1115; color:#e6e6e6;
         display:flex; justify-content:center; padding:40px 16px; margin:0; }
  .card { background:#1a1d24; border:1px solid #2a2e38; border-radius:12px;
          padding:28px 32px; max-width:380px; width:100%; text-align:center; }
  h1 { font-size:20px; margin:0 0 6px; }
  .sub { color:#8b93a5; font-size:13px; margin-bottom:20px; }
  #qr-box { min-height:230px; display:flex; align-items:center; justify-content:center;
            background:#fff; border-radius:10px; margin-bottom:16px; padding:12px; }
  #qr-box img { width:220px; height:220px; }
  #qr-box .placeholder { color:#555; font-size:14px; }
  button { background:#4c7dff; color:#fff; border:none; border-radius:8px;
           padding:12px 24px; font-size:15px; cursor:pointer; width:100%; }
  button:disabled { background:#3a4a6b; cursor:not-allowed; }
  #status { margin-top:14px; font-size:14px; min-height:20px; color:#aab;
            word-break:break-all; }
  #status.ok { color:#57d98a; }
  #status.err { color:#ff6b6b; }
</style>
</head>
<body>
<div class="card">
  <h1>🎬 B站登录管理</h1>
  <div class="sub">扫码登录后自动写入 bili-api 的 cookies.txt</div>
  <div id="qr-box"><div class="placeholder">点击下方按钮获取二维码</div></div>
  <button id="btn" onclick="getQr()">📱 获取二维码</button>
  <div id="status"></div>
</div>
<script>
let timer = null;
const $ = id => document.getElementById(id);

function setStatus(msg, cls) {
  const el = $("status");
  el.className = cls || "";
  el.textContent = msg;
}

async function getQr() {
  $("btn").disabled = true;
  setStatus("正在获取二维码...");
  try {
    const r = await fetch("/api/qrcode");
    const d = await r.json();
    if (!d.qr_base64) { setStatus("获取失败: " + (d.error || "未知"), "err"); return; }
    $("qr-box").innerHTML = '<img src="data:image/png;base64,' + d.qr_base64 + '">';
    setStatus("请用手机 B站 APP 扫码");
    if (timer) clearInterval(timer);
    timer = setInterval(poll, 3000);
  } catch (e) {
    setStatus("请求失败: " + e, "err");
  }
}

async function poll() {
  try {
    const r = await fetch("/api/poll");
    const d = await r.json();
    if (d.status === "success") {
      clearInterval(timer);
      setStatus("✅ 登录成功！cookie 已写入 cookies.txt（" + d.preview + "…）", "ok");
      $("btn").disabled = false;
    } else if (d.status === "scanned") {
      setStatus("📱 已扫码，请在手机上确认登录");
    } else if (d.status === "expired") {
      clearInterval(timer);
      setStatus("⏰ 二维码已过期，请重新获取", "err");
      $("btn").disabled = false;
    } else {
      setStatus("⏳ 等待扫码...");
    }
  } catch (e) {
    // 忽略瞬时网络错误，下次轮询重试
  }
}
</script>
</body>
</html>
"""


@app.get("/")
def index():
    return Response(HTML, content_type="text/html; charset=utf-8")


@app.get("/api/qrcode")
def api_qrcode():
    try:
        s = requests.Session()
        s.headers.update({"User-Agent": UA})
        r = s.get(GENERATE_URL, timeout=10).json()
        if r.get("code") != 0:
            return jsonify(error=r.get("message", "获取二维码失败"))
        key = r["data"]["qrcode_key"]
        url = r["data"]["url"]
        img = qrcode.make(url)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        STATE.update(session=s, key=key, ts=time.time())
        return jsonify(qr_base64=base64.b64encode(buf.getvalue()).decode())
    except Exception as e:
        return jsonify(error=repr(e))


@app.get("/api/poll")
def api_poll():
    s = STATE.get("session")
    key = STATE.get("key")
    if not s or not key:
        return jsonify(status="no_qr")
    try:
        d = s.get(POLL_URL, params={"qrcode_key": key}, timeout=10).json()
        code = d["data"]["code"]
    except Exception:
        return jsonify(status="waiting")
    if code == 0:
        cookies = "; ".join(f"{c.name}={c.value}" for c in s.cookies)
        try:
            with open(COOKIE_FILE, "w", encoding="utf-8") as f:
                f.write(cookies)
        except OSError as e:
            return jsonify(status="success", preview=f"cookie 写入失败: {e!r}")
        STATE.clear()
        return jsonify(status="success", preview=cookies[:60])
    if code == 86090:
        return jsonify(status="scanned")
    if code == 86038:
        return jsonify(status="expired")
    return jsonify(status="waiting")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8123)
