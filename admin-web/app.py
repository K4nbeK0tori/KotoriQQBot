"""薇欧拉管理台：统一面板壳 + AI 管理（角色卡/群开关/管理员/调试）+ B站登录。

页面：
    /login   账号密码登录页
    /        统一面板壳（左侧菜单 + iframe）
    /admin    AI 管理页
    /bili     B站登录页
API（JSON）：
    /api/login、/api/logout
    /api/roles、/api/roles/<name>、/api/default_role
    /api/groups、/api/groups/<gid>
    /api/admins、/api/admins/<qq>
    /api/test（DeepSeek 连通性测试）
    /api/qrcode、/api/poll（B站扫码登录）
"""

import base64
import io
import json
import os
import re
import secrets
import time
import urllib.request
from functools import wraps

import qrcode
import requests
from flask import (
    Flask,
    Response,
    jsonify,
    redirect,
    request,
    session,
)

app = Flask(__name__)

DATA_DIR = "/data"
ADMIN_DATA = os.path.join(DATA_DIR, "admin_data.json")
ROLE_DIR = os.path.join(DATA_DIR, "role_cards")
COOKIE_FILE = "/data/cookies.txt"

# 默认登录账号密码（首次初始化写入 admin_data.json，可在服务器改文件）
DEFAULT_WEB_USER = "admin"
DEFAULT_WEB_PASS = "ILoveChara233."


def _ensure_web_auth():
    """初始化登录账号密码与 session 密钥（存 admin_data.json）。"""
    a = _admin()
    changed = False
    if not a.get("web_user"):
        a["web_user"] = DEFAULT_WEB_USER
        changed = True
    if not a.get("web_pass"):
        a["web_pass"] = DEFAULT_WEB_PASS
        changed = True
    if not a.get("web_secret"):
        a["web_secret"] = secrets.token_hex(32)
        changed = True
    if changed:
        _write_json(ADMIN_DATA, a)
    app.secret_key = a["web_secret"]


def _web_auth_ok(user, password) -> bool:
    a = _admin()
    return str(user) == str(a.get("web_user")) and str(password) == str(
        a.get("web_pass")
    )


def require_auth(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("auth"):
            if request.path.startswith("/api/"):
                return jsonify(error="未登录"), 401
            return redirect("/login")
        return f(*args, **kwargs)

    return wrapper


@app.before_request
def _auth_check():
    """统一 API 登录检查（login/logout 除外）。"""
    if request.path.startswith("/api/") and request.path not in (
        "/api/login",
        "/api/logout",
    ):
        if not session.get("auth"):
            return jsonify(error="未登录"), 401

DATA_DIR = "/data"
ADMIN_DATA = os.path.join(DATA_DIR, "admin_data.json")
ROLE_DIR = os.path.join(DATA_DIR, "role_cards")
COOKIE_FILE = "/data/cookies.txt"

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_URL = os.environ.get("DEEPSEEK_URL", "https://api.deepseek.com/v1/chat/completions")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")

BILI_GENERATE = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
BILI_POLL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"
UA_BROWSER = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# ===== 数据读写 =====


def _read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _admin():
    return _read_json(ADMIN_DATA, {"superadmins": [], "groups": {}, "default_role": "viola"})


def _roles():
    roles = []
    if os.path.isdir(ROLE_DIR):
        for fn in sorted(os.listdir(ROLE_DIR)):
            if fn.endswith(".json"):
                r = _read_json(os.path.join(ROLE_DIR, fn), None)
                if r:
                    roles.append(r)
    return roles


def _role_path(name):
    return os.path.join(ROLE_DIR, f"{name}.json")


# ===== 页面 =====

SHELL_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>🌸 薇欧拉管理台</title>
<link rel="stylesheet" href="/static/sakura.css">
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  body { font-family:system-ui,sans-serif; display:flex; height:100vh; }
  aside { width:200px; background:linear-gradient(180deg,#ffd9e3,#ffc2d4); padding:18px 0;
          display:flex; flex-direction:column; box-shadow:2px 0 12px rgba(255,150,180,.25); }
  aside .logo { color:#c0557f; font-weight:700; font-size:18px; padding:0 20px 14px; border-bottom:1px solid #ffb9cd; margin-bottom:12px; }
  aside a { color:#b04a70; text-decoration:none; padding:11px 20px; font-size:14px; border-left:3px solid transparent; }
  aside a:hover { background:#ffe0ea; border-left-color:#e87ba5; }
  aside a.active { background:#fff0f5; border-left-color:#e87ba5; font-weight:600; }
  main { flex:1; padding:0; }
  iframe { width:100%; height:100%; border:none; }
</style>
</head>
<body>
<aside>
  <div class="logo">🌸 薇欧拉管理台</div>
  <a href="/admin" target="main">🤖 AI 管理</a>
  <a href="/bili" target="main">🎬 B站登录</a>
  <a href="https://napcat.kanbekotori.top/webui" target="_blank">🐱 NapCat 面板</a>
  <a href="javascript:logout()" style="margin-top:auto;border-top:1px solid #ffb9cd;padding-top:14px;">🚪 退出登录</a>
</aside>
<main><iframe name="main" src="/admin"></iframe></main>
<script>
function logout() {
  fetch("/api/logout", { method: "POST" }).then(() => { location.href = "/login"; });
}
</script>
<script src="/static/sakura.js"></script>
</body>
</html>"""

ADMIN_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI 管理</title>
<link rel="stylesheet" href="/static/sakura.css">
<style>
  body { font-family:system-ui,sans-serif; background:#fff5f8; color:#4a2b3a; padding:24px; }
  h2 { color:#c0557f; margin:0 0 16px; }
  h3 { color:#c0557f; margin:24px 0 10px; }
  .card { background:#fff; border:1px solid #ffd3e0; border-radius:12px; padding:18px; margin-bottom:14px; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th { background:#ffe4ec; color:#b04a70; text-align:left; padding:8px 10px; }
  td { padding:8px 10px; border-bottom:1px solid #ffeef4; }
  input,select,textarea { padding:6px 8px; border:1px solid #ffc9da; border-radius:6px; font-size:13px; }
  button { background:#e87ba5; color:#fff; border:none; border-radius:6px; padding:6px 14px; cursor:pointer; font-size:13px; }
  button:hover { background:#d5668f; }
  button.ghost { background:#fff; color:#c0557f; border:1px solid #ffc9da; }
  .msg { margin:8px 0; padding:8px 12px; border-radius:6px; font-size:13px; display:none; }
  .msg.ok { display:block; background:#e8f9ee; color:#2e7d4f; }
  .msg.err { display:block; background:#fdeaea; color:#b04a5a; }
  .role-edit { display:none; }
  .role-edit.open { display:block; background:#fff0f5; border:1px solid #ffc9da; border-radius:10px; padding:14px; margin-top:10px; }
  textarea { width:100%; min-height:120px; }
  label { font-size:13px; color:#8a5a70; margin-right:6px; }
</style>
</head>
<body>
<h2>🌸 AI 管理</h2>
<div id="msg" class="msg"></div>

<div class="card">
  <h3 style="margin-top:0;">角色卡</h3>
  <table>
    <tr><th>名称</th><th>简介</th><th>默认</th><th>操作</th></tr>
    <tbody id="role-tbody"></tbody>
  </table>
  <div style="margin-top:10px;">
    <button onclick="newRole()">➕ 新建角色卡</button>
  </div>
  <div style="margin-top:12px;">
    <label>👑 超管专属角色卡（超管@时使用）：</label>
    <select id="admin-role" onchange="setAdminRole(this.value)" style="max-width:200px;"></select>
  </div>
  <div id="role-edit" class="role-edit"></div>
</div>

<div class="card">
  <h3 style="margin-top:0;">群开关</h3>
  <table>
    <tr><th>群号</th><th>AI</th></tr>
    <tbody id="group-tbody"></tbody>
  </table>
  <div style="margin-top:10px;">
    <input id="new-gid" placeholder="群号" style="width:120px">
    <button onclick="addGroup()">➕ 添加群</button>
    <span style="font-size:12px;color:#a07a8c;">（也可以在群里让管理员发 /ai on 自动添加）</span>
  </div>
</div>

<div class="card">
  <h3 style="margin-top:0;">超级管理员</h3>
  <table>
    <tr><th>QQ 号</th><th>操作</th></tr>
    <tbody id="admin-tbody"></tbody>
  </table>
  <div style="margin-top:10px;">
    <input id="new-admin" placeholder="QQ号" style="width:140px">
    <button onclick="addAdmin()">➕ 添加</button>
  </div>
</div>

<div class="card">
  <h3 style="margin-top:0;">DeepSeek 调试</h3>
  <label>测试提示词：</label>
  <input id="test-prompt" placeholder="你好呀" style="width:260px">
  <button onclick="testApi()">🧪 测试</button>
  <div id="test-result" style="margin-top:8px;font-size:13px;white-space:pre-wrap;"></div>
</div>

<div class="card">
  <h3 style="margin-top:0;">📊 Token 用量（近 14 天）</h3>
  <div id="usage-chart" style="display:flex;align-items:flex-end;gap:6px;height:140px;padding:10px 0;"></div>
  <div id="usage-total" style="font-size:13px;color:#8a5a70;margin-top:6px;"></div>
</div>

<script>
const $ = id => document.getElementById(id);

function show(msg, ok) {
  const el = $("msg");
  el.className = "msg " + (ok ? "ok" : "err");
  el.textContent = msg;
  setTimeout(() => { el.className = "msg"; }, 4000);
}

async function api(path, method, body) {
  const opt = { method: method || "GET", headers: { "Content-Type": "application/json" } };
  if (body) opt.body = JSON.stringify(body);
  const r = await fetch(path, opt);
  const d = await r.json();
  if (!r.ok) throw new Error(d.error || r.status);
  return d;
}

async function loadRoles() {
  const roles = await api("/api/roles");
  const admin = await api("/api/config");
  const tbody = $("role-tbody");
  tbody.innerHTML = "";
  for (const r of roles) {
    const tr = document.createElement("tr");
    const isDefault = r.name === admin.default_role;
    tr.innerHTML = `<td>${r.name}</td><td>${(r.description || "").slice(0, 30)}</td>
      <td>${isDefault ? "⭐" : ""}</td>
      <td>
        <button onclick="editRole('${r.name}')">编辑</button>
        ${isDefault ? "" : `<button class="ghost" onclick="setDefault('${r.name}')">设默认</button>`}
        <button class="ghost" onclick="delRole('${r.name}')">删除</button>
      </td>`;
    tbody.appendChild(tr);
  }
  // 超管专属角色卡下拉
  const sel = $("admin-role");
  sel.innerHTML = '<option value="">（无，跟随群角色）</option>' +
    roles.map(r => `<option value="${r.name}" ${r.name === admin.admin_role ? "selected" : ""}>${r.name}</option>`).join("");
}

async function setAdminRole(name) {
  try {
    await api("/api/admin_role", "POST", { name });
    show(name ? "超管专属角色卡已设为「" + name + "」" : "已取消超管专属角色卡", true);
  } catch (e) { show(e.message, false); }
}

async function editRole(name) {
  const roles = await api("/api/roles");
  const r = roles.find(x => x.name === name);
  if (!r) return;
  const box = $("role-edit");
  box.className = "role-edit open";
  box.innerHTML = `
    <label>名称</label><input id="re-name" value="${r.name}"><br><br>
    <label>简介</label><input id="re-desc" value="${r.description || ""}" style="width:100%"><br><br>
    <label>人设（system prompt）</label><br>
    <textarea id="re-system">${r.system || ""}</textarea><br><br>
    <button onclick="saveRole('${r.name}')">💾 保存</button>
    <button class="ghost" onclick="document.getElementById('role-edit').className='role-edit'">取消</button>`;
}

function newRole() {
  const box = $("role-edit");
  box.className = "role-edit open";
  box.innerHTML = `
    <label>名称</label><input id="re-name" placeholder="角色卡文件名（英文）"><br><br>
    <label>简介</label><input id="re-desc" placeholder="一句话介绍" style="width:100%"><br><br>
    <label>人设（system prompt）</label><br>
    <textarea id="re-system" placeholder="你是……"></textarea><br><br>
    <button onclick="saveRole('')">💾 新建</button>
    <button class="ghost" onclick="document.getElementById('role-edit').className='role-edit'">取消</button>`;
}

async function saveRole(oldName) {
  const name = $("re-name").value.trim();
  const description = $("re-desc").value.trim();
  const system = $("re-system").value.trim();
  if (!name || !system) { show("名称和人设不能为空", false); return; }
  try {
    if (oldName) {
      await api("/api/roles/" + oldName, "DELETE");
    }
    await api("/api/roles", "POST", { name, description, system });
    show("角色卡已保存喵", true);
    loadRoles();
    loadGroups();
    document.getElementById("role-edit").className = "role-edit";
  } catch (e) { show("保存失败: " + e.message, false); }
}

async function delRole(name) {
  if (!confirm("删除角色卡 " + name + "？")) return;
  try {
    await api("/api/roles/" + name, "DELETE");
    show("已删除", true);
    loadRoles();
    loadGroups();
  } catch (e) { show(e.message, false); }
}

async function setDefault(name) {
  try {
    await api("/api/default_role", "POST", { name });
    show("已设为默认角色", true);
    loadRoles();
    loadGroups();
  } catch (e) { show(e.message, false); }
}

async function loadGroups() {
  const groups = await api("/api/groups");
  const tbody = $("group-tbody");
  tbody.innerHTML = "";
  for (const [gid, g] of Object.entries(groups)) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${gid}</td>
      <td><input type="checkbox" ${g.enabled ? "checked" : ""} onchange="setGroup('${gid}', this.checked)"></td>`;
    tbody.appendChild(tr);
  }
}

async function addGroup() {
  const gid = $("new-gid").value.trim();
  if (!gid) return;
  try {
    await api("/api/groups/" + gid, "POST", { enabled: false });
    $("new-gid").value = "";
    loadGroups();
  } catch (e) { show(e.message, false); }
}

async function setGroup(gid, enabled) {
  await api("/api/groups/" + gid, "POST", { enabled });
}

async function loadAdmins() {
  const admins = await api("/api/admins");
  const tbody = $("admin-tbody");
  tbody.innerHTML = "";
  for (const qq of admins) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${qq}</td><td><button class="ghost" onclick="delAdmin('${qq}')">删除</button></td>`;
    tbody.appendChild(tr);
  }
}

async function addAdmin() {
  const qq = $("new-admin").value.trim();
  if (!qq) return;
  try {
    await api("/api/admins", "POST", { qq });
    $("new-admin").value = "";
    loadAdmins();
  } catch (e) { show(e.message, false); }
}

async function delAdmin(qq) {
  try {
    await api("/api/admins/" + qq, "DELETE");
    loadAdmins();
  } catch (e) { show(e.message, false); }
}

async function testApi() {
  const prompt = $("test-prompt").value.trim() || "你好呀";
  const el = $("test-result");
  el.textContent = "测试中喵……";
  try {
    const d = await api("/api/test", "POST", { prompt });
    el.textContent = "✅ " + d.reply;
  } catch (e) { el.textContent = "❌ " + e.message; }
}

async function loadUsage() {
  try {
    const d = await api("/api/usage");
    const chart = $("usage-chart");
    chart.innerHTML = "";
    const days = d.days || [];
    const max = Math.max(1, ...days.map(x => x.prompt + x.completion));
    for (const item of days) {
      const total = item.prompt + item.completion;
      const h = Math.max(4, Math.round((total / max) * 110));
      const col = document.createElement("div");
      col.style.cssText = "display:flex;flex-direction:column;align-items:center;flex:1;gap:4px;";
      col.innerHTML =
        `<div style="width:100%;height:${h}px;background:linear-gradient(180deg,#ff9eb8,#e87ba5);border-radius:6px 6px 0 0;" title="${item.day}: ${total} tokens"></div>` +
        `<div style="font-size:10px;color:#a07a8c;transform:rotate(-30deg);white-space:nowrap;">${item.day.slice(5)}</div>`;
      chart.appendChild(col);
    }
    $("usage-total").textContent = "近 14 天累计：约 " + (d.total || 0) + " tokens";
  } catch (e) {
    $("usage-total").textContent = "用量数据读取失败";
  }
}

loadRoles();
loadGroups();
loadAdmins();
loadUsage();
</script>
<script src="/static/sakura.js"></script>
</body>
</html>"""

BILI_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>B站登录</title>
<link rel="stylesheet" href="/static/sakura.css">
<style>
  body { font-family:system-ui,sans-serif; background:#fff5f8; color:#4a2b3a; padding:24px; display:flex; justify-content:center; }
  .card { background:#fff; border:1px solid #ffd3e0; border-radius:12px; padding:24px; max-width:380px; width:100%; text-align:center; }
  h2 { color:#c0557f; margin:0 0 6px; }
  .sub { color:#a07a8c; font-size:13px; margin-bottom:18px; }
  #qr-box { min-height:230px; display:flex; align-items:center; justify-content:center; background:#fff;
            border-radius:10px; margin-bottom:16px; padding:12px; border:1px dashed #ffc9da; }
  #qr-box img { width:220px; height:220px; }
  #qr-box .placeholder { color:#b58a9c; font-size:14px; }
  button { background:#e87ba5; color:#fff; border:none; border-radius:8px; padding:12px 24px; font-size:15px; cursor:pointer; width:100%; }
  button:disabled { background:#f0b9cb; cursor:not-allowed; }
  #status { margin-top:14px; font-size:14px; color:#8a5a70; word-break:break-all; }
  #status.ok { color:#2e7d4f; }
  #status.err { color:#b04a5a; }
</style>
</head>
<body>
<div class="card">
  <h2>🎬 B站登录</h2>
  <div class="sub">扫码后自动写入 cookies.txt（bili-api 下载用它）</div>
  <div id="qr-box"><div class="placeholder">点击下方按钮获取二维码</div></div>
  <button id="btn" onclick="getQr()">📱 获取二维码</button>
  <div id="status"></div>
</div>
<script>
let timer = null;
const $ = id => document.getElementById(id);
function setStatus(msg, cls) { const el = $("status"); el.className = cls || ""; el.textContent = msg; }

async function getQr() {
  $("btn").disabled = true;
  setStatus("正在获取二维码...");
  try {
    const d = await (await fetch("/api/qrcode")).json();
    if (!d.qr_base64) { setStatus("获取失败: " + (d.error || "未知"), "err"); return; }
    $("qr-box").innerHTML = '<img src="data:image/png;base64,' + d.qr_base64 + '">';
    setStatus("请用手机 B站 APP 扫码");
    if (timer) clearInterval(timer);
    timer = setInterval(poll, 3000);
  } catch (e) { setStatus("请求失败: " + e, "err"); }
}

async function poll() {
  try {
    const d = await (await fetch("/api/poll")).json();
    if (d.status === "success") {
      clearInterval(timer);
      setStatus("✅ 登录成功！cookie 已写入（" + d.preview + "…）", "ok");
      $("btn").disabled = false;
    } else if (d.status === "scanned") { setStatus("📱 已扫码，请在手机上确认登录"); }
    else if (d.status === "expired") {
      clearInterval(timer);
      setStatus("⏰ 二维码已过期，请重新获取", "err");
      $("btn").disabled = false;
    } else { setStatus("⏳ 等待扫码..."); }
  } catch (e) {}
}
</script>
<script src="/static/sakura.js"></script>
</body>
</html>"""


LOGIN_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>🌸 薇欧拉管理台 · 登录</title>
<link rel="stylesheet" href="/static/sakura.css">
</head>
<body>
<div class="login-wrap">
  <div class="login-card glass">
    <h1>🌸 薇欧拉管理台</h1>
    <div class="sub">梦限大的猫娘女仆薇欧拉，为老师服务喵～</div>
    <input id="user" placeholder="账号" autocomplete="username">
    <input id="pass" type="password" placeholder="密码" autocomplete="current-password">
    <button onclick="login()">登 录</button>
    <div id="err" class="err"></div>
  </div>
</div>
<script>
async function login() {
  const user = document.getElementById("user").value.trim();
  const pass = document.getElementById("pass").value;
  if (!user || !pass) { document.getElementById("err").textContent = "账号和密码不能为空喵"; return; }
  try {
    const r = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user, pass })
    });
    const d = await r.json();
    if (d.ok) { location.href = "/"; }
    else { document.getElementById("err").textContent = d.error || "登录失败"; }
  } catch (e) { document.getElementById("err").textContent = "网络错误: " + e; }
}
document.getElementById("pass").addEventListener("keydown", e => { if (e.key === "Enter") login(); });
</script>
<script src="/static/sakura.js"></script>
</body>
</html>"""


@app.get("/login")
def login_page():
    if session.get("auth"):
        return redirect("/")
    return Response(LOGIN_HTML, content_type="text/html; charset=utf-8")


@app.post("/api/login")
def api_login():
    body = request.get_json(silent=True) or {}
    if _web_auth_ok(body.get("user", ""), body.get("pass", "")):
        session["auth"] = True
        return jsonify(ok=True)
    return jsonify(error="账号或密码错误喵"), 401


@app.post("/api/logout")
def api_logout():
    session.clear()
    return jsonify(ok=True)


@app.get("/")
@require_auth
def shell():
    return Response(SHELL_HTML, content_type="text/html; charset=utf-8")


@app.get("/admin")
@require_auth
def admin_page():
    return Response(ADMIN_HTML, content_type="text/html; charset=utf-8")


@app.get("/bili")
@require_auth
def bili_page():
    return Response(BILI_HTML, content_type="text/html; charset=utf-8")


# ===== AI 管理 API =====


@app.get("/api/config")
def api_config():
    a = _admin()
    return jsonify(
        {
            "default_role": a.get("default_role", "viola"),
            "admin_role": a.get("admin_role") or "",
        }
    )


@app.post("/api/admin_role")
def api_admin_role():
    body = request.get_json(silent=True) or {}
    name = str(body.get("name", "")).strip()
    a = _admin()
    if name and not os.path.exists(_role_path(name)):
        return jsonify(error="角色卡不存在"), 400
    a["admin_role"] = name or None
    _write_json(ADMIN_DATA, a)
    return jsonify(ok=True)


@app.get("/api/roles")
def api_roles():
    return jsonify(_roles())


@app.post("/api/roles")
def api_role_save():
    body = request.get_json(silent=True) or {}
    name = str(body.get("name", "")).strip()
    if not name or not re.match(r"^[A-Za-z0-9_\-\u4e00-\u9fa5]{1,40}$", name):
        return jsonify(error="角色名不合法"), 400
    target = _role_path(name)
    if os.path.exists(target):
        return jsonify(error=f"角色卡「{name}」已存在，新建不能用同名（编辑请点该卡的编辑）"), 409
    os.makedirs(ROLE_DIR, exist_ok=True)
    _write_json(target, {"name": name, "description": body.get("description", ""), "system": body.get("system", "")})
    return jsonify(ok=True)


@app.delete("/api/roles/<name>")
def api_role_del(name):
    if not re.match(r"^[A-Za-z0-9_\-\u4e00-\u9fa5]{1,40}$", name):
        return jsonify(error="角色名不合法"), 400
    p = _role_path(name)
    if os.path.exists(p):
        os.remove(p)
    a = _admin()
    if a.get("default_role") == name:
        a["default_role"] = "viola"
        _write_json(ADMIN_DATA, a)
    if a.get("admin_role") == name:
        a["admin_role"] = None
        _write_json(ADMIN_DATA, a)
    for gid, g in (a.get("groups") or {}).items():
        if g.get("role") == name:
            g["role"] = a["default_role"]
    _write_json(ADMIN_DATA, a)
    return jsonify(ok=True)


@app.post("/api/default_role")
def api_default():
    body = request.get_json(silent=True) or {}
    name = str(body.get("name", "")).strip()
    if not os.path.exists(_role_path(name)):
        return jsonify(error="角色卡不存在"), 400
    a = _admin()
    a["default_role"] = name
    _write_json(ADMIN_DATA, a)
    return jsonify(ok=True)


@app.get("/api/groups")
def api_groups():
    a = _admin()
    return jsonify(a.get("groups", {}))


@app.post("/api/groups/<gid>")
def api_group_set(gid):
    body = request.get_json(silent=True) or {}
    a = _admin()
    g = a.setdefault("groups", {}).setdefault(str(gid), {})
    if "enabled" in body:
        g["enabled"] = bool(body["enabled"])
    if "role" in body and body["role"]:
        g["role"] = str(body["role"])
    _write_json(ADMIN_DATA, a)
    return jsonify(ok=True)


@app.get("/api/admins")
def api_admins():
    a = _admin()
    return jsonify(a.get("superadmins", []))


@app.post("/api/admins")
def api_admin_add():
    body = request.get_json(silent=True) or {}
    qq = str(body.get("qq", "")).strip()
    if not qq.isdigit():
        return jsonify(error="QQ 号格式不对"), 400
    a = _admin()
    admins = a.setdefault("superadmins", [])
    if qq not in [str(x) for x in admins]:
        admins.append(qq)
    _write_json(ADMIN_DATA, a)
    return jsonify(ok=True)


@app.delete("/api/admins/<qq>")
def api_admin_del(qq):
    a = _admin()
    a["superadmins"] = [x for x in a.get("superadmins", []) if str(x) != str(qq)]
    _write_json(ADMIN_DATA, a)
    return jsonify(ok=True)


@app.post("/api/test")
def api_test():
    body = request.get_json(silent=True) or {}
    prompt = str(body.get("prompt", "")).strip() or "你好"
    if not DEEPSEEK_API_KEY:
        return jsonify(error="DEEPSEEK_API_KEY 未配置（看 .env）"), 400
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 200,
    }
    try:
        req = urllib.request.Request(
            DEEPSEEK_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return jsonify(reply=data["choices"][0]["message"]["content"])
    except Exception as e:
        return jsonify(error=repr(e)), 502


@app.get("/api/usage")
def api_usage():
    """Token 用量统计（按天），供面板图表展示。"""
    path = os.path.join(DATA_DIR, "usage.json")
    data = _read_json(path, {})
    days = sorted(data.keys())[-14:]
    result = []
    for d in days:
        u = data[d]
        result.append(
            {"day": d, "prompt": u.get("prompt", 0), "completion": u.get("completion", 0)}
        )
    total = sum(x["prompt"] + x["completion"] for x in result)
    return jsonify({"days": result, "total": total})


# ===== B站扫码登录 =====

STATE: dict = {"session": None, "key": None, "ts": 0.0}


@app.get("/api/qrcode")
def api_qrcode():
    try:
        s = requests.Session()
        s.headers.update({"User-Agent": UA_BROWSER})
        r = s.get(BILI_GENERATE, timeout=10).json()
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
        d = s.get(BILI_POLL, params={"qrcode_key": key}, timeout=10).json()
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
    _ensure_web_auth()
    os.makedirs(ROLE_DIR, exist_ok=True)
    app.run(host="0.0.0.0", port=8133)
