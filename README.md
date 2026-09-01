<div align="center">

# 🌸 B站视频下载姬 · 薇欧拉

<p style="color:#d4698f; font-size:1.1em; margin:0.4em 0;">
  来自梦限大的猫娘女仆薇欧拉，为各位老师服务喵～
  谁在群里发 B站链接，薇欧拉就帮你把视频剪辑好发到群里，还附上信息卡片哦！
</p>

**🎀 温馨提示：薇欧拉说话带喵，剪辑失败还会给你讲小故事喵 🎀**

</div>

---

## ✨ 功能特性

- 🎬 **自动下载视频**：群友发 B站链接 / 短链 / BV 号，自动下载视频（默认 1080P）并直接发到群里
- 📨 **剪辑提示**：收到链接先打招呼「已经收到老师发送的（BV号），薇欧拉酱正在善意剪辑哦~」
- 📝 **信息卡片**：标题 / UP主 / 时长 / 播放量 / 点赞 + 封面图
- 😿 **失败卖萌**：下载失败或视频太大时，薇欧拉会讲个小故事安慰老师喵
- 🧹 **发送后即清理**：视频发完自动删除服务器本地文件，不占磁盘
- 🔄 **网页续登录**：B站 cookie 过期？浏览器打开登录页扫码即可，不用碰服务器
- 🔒 **端口安全**：服务只绑定本机回环地址，公网不可达，外部无法直接访问

---

## 🧩 集成了什么

| 组件 | 在项目中扮演的角色 | 说明 |
|---|---|---|
| 🐱 **NapCat** | QQ 协议端 | 登录 QQ、收发消息，暴露 OneBot 11 协议 |
| 🤖 **NoneBot2** | 机器人框架 | 消息处理、插件调度（薇欧拉的脑子喵） |
| 📥 **bili-api** | 视频下载服务 | 抓取 B站页面 `__playinfo__`，后台下载 + FFmpeg 合并（第三方开源） |
| 🎫 **bili-web** | 登录管理页 | 自研小页面：浏览器扫码续 B站 cookie，免 SSH |
| 🌐 **Caddy** | 域名反代 | 自动 HTTPS，把面板和登录页反代到域名 |
| 🐳 **Docker Compose** | 一键部署 | 三个容器一条命令拉起 |
| ☁️ **GitHub** | 代码备份 | 私有仓库同步全部代码与配置 |

> 🔗 第三方组件：[NapCat](https://github.com/NapNeko/NapCatQQ) · [NoneBot2](https://nonebot.dev) · [bili-api](https://github.com/Suxiaoqinx/bilibili) · [Caddy](https://caddyserver.com)

---

## 🏗️ 架构

```
公网：仅 Caddy（HTTPS 反代面板与登录页）
│
└── 服务器本机（回环地址）────────────────────
    NapCat（QQ 协议端）
    ├─ OneBot WS   ←── bot 内网直连
    └─ WebUI       ←── Caddy 反代 → 面板域名

    NoneBot2 + bili_parser（逻辑端）
        ├─ 调 view API 拿视频信息
        └─ 调 bili-api（内网，无端口映射）下载
                     │
    bili-api（下载服务）→ 出站访问 B站（带登录 cookie）
    bili-web（登录管理页）→ Caddy 反代 → 登录页域名
```

- 三个容器共享 `downloads` 目录：bot 写 → NapCat 发 → 发送后删除
- `bot` / `bili-api` 零端口映射，公网完全不可达
- Caddy 负责面板与登录页的 HTTPS 反代

---

## 🚀 用法（部署）

> 部署文档不含任何账号密码，需要的密钥请自行保管在服务器 `.env` 中喵。

### 环境要求

- 低配服务器即可（实测 1C1G / Debian 13 顺畅运行）
- Docker + Docker Compose

### 部署步骤

```bash
# 1. 拉取代码
git clone https://github.com/<你的用户名>/<仓库名>.git
cd qqbot

# 2. 配置 .env（含你的自定义密钥）
cp .env.example .env && vi .env

# 3. 一键启动（自动构建三个容器）
bash deploy.sh
```

### 登录配置（一次性）

1. **QQ 登录**：通过面板域名（Caddy 反代 NapCat WebUI）扫码登录 QQ
2. **B站 cookie**：通过登录页域名（Caddy 反代 bili-web）扫码获取，自动写入
3. **配置 OneBot WS**：NapCat 面板里开一个正向 WebSocket，端口与 `docker-compose.yml` 一致

### 使用

群里发 B站链接 / b23.tv 短链 / BV 号 → 薇欧拉自动回复：剪辑提示 → 信息卡片 → 视频，发送后自动清理。

---

## 🔧 日常维护

```bash
docker compose logs -f bot        # 机器人日志（下载/发送流程）
docker compose logs -f bili-api   # 下载服务日志
docker compose logs -f napcat     # NapCat 日志
docker compose restart bot        # 改插件后重启
git pull && bash deploy.sh        # 更新
```

- **B站 cookie 过期**：打开登录页域名扫码即可（无需 SSH）
- **QQ 登录态**：持久化在挂载目录，重建容器不掉线
- **防刷**：同一会话 120 秒内只处理一次，防止把服务器榨干喵

---

## ⚠️ 注意事项

- 📛 **封号风险**：QQ 机器人基于非官方协议，请使用小号并低调使用
- ⚖️ **版权提醒**：视频下载分发仅供个人学习研究，请遵守平台条款
- 🔒 **安全**：面板与登录页均建议加一层访问密码（Caddy `basicauth`），不要暴露端口给公网

---

<div align="center">

<p style="color:#d4698f;">🌸 薇欧拉会一直在这里等老师们发视频喵～ 🌸</p>

</div>
