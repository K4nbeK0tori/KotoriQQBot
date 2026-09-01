# QQ 机器人：B站视频解析（NapCat + NoneBot2）

在 1C1G 低配服务器（Debian 13）上运行，监听群消息中的哔哩哔哩视频链接，
自动解析标题 / UP主 / 播放量 / 封面并回复到群里。

> 与服务器上的 xray(VPN) 服务共存：所有端口一律绑定 `127.0.0.1`，
> **不占用任何公网端口**，与 xray 的 443/80 零冲突。

## 架构

```
公网：仅 xray :443（VPN 独占）
│
└── 本机 127.0.0.1 ────────────────────────────
    NapCat（协议端）
    ├─ OneBot11 WS   127.0.0.1:3001  ←── bot 容器内网直连 napcat:3001
    └─ WebUI         127.0.0.1:6099  ←── ssh 隧道访问，登录/管理
                                            │
    NoneBot2 + bili_parser（逻辑端）─────────┤
        └── 出站调用 B站公开 API（无需入站端口）
```

- **napcat**：`mlikiowa/napcat-docker`，登录 QQ、收发消息，暴露 OneBot 11 协议
- **bot**：NoneBot2 + 自写 `bili_parser` 插件，解析 B站链接并回复
- 端口 3001/6099 只监听回环地址，公网不可达；`bot` 容器零端口映射

## 目录结构

```
├── docker-compose.yml     # NapCat + bot 编排（全绑 127.0.0.1）
├── .env.example           # 配置模板（复制为 .env，不提交 git）
├── deploy.sh              # 服务器一键部署脚本
├── napcat/                # NapCat 挂载目录（config/qq，运行时数据，不提交 git）
└── bot/
    ├── Dockerfile
    ├── requirements.txt
    ├── bot.py             # NoneBot2 入口
    └── plugins/bili_parser/__init__.py   # B站解析插件
```

## 第一步：推到 GitHub 备份

GitHub 上新建一个私有仓库（`qqbot`），然后：

```bash
git remote add origin https://github.com/<你的用户名>/<仓库名>.git
git branch -M main
git push -u origin main
```

> `.env`、`napcat/config`、`napcat/qq` 已被 `.gitignore` 排除，
> 登录态 / token 不会进仓库。

## 第二步：服务器部署（Debian 13）

### 1. 准备环境（一次性）

```bash
# 安装 Docker
curl -fsSL https://get.docker.com | sh

# 加 swap（1G 内存必做，防 OOM）
fallocate -l 2G /swapfile && chmod 600 /swapfile
mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab

# 可选：Docker 国内镜像加速（拉镜像慢时）
# 编辑 /etc/docker/daemon.json，填入 registry-mirrors 后 systemctl restart docker
```

### 2. 拉代码并配置

```bash
mkdir -p /opt/qqbot && cd /opt/qqbot
bash deploy.sh https://github.com/<你的用户名>/<仓库名>.git
# 第一次执行：克隆代码 + 生成 .env 模板后自动退出
vi .env               # 改 NAPCAT_UID/GID、NAPCAT_WEBUI_TOKEN
bash deploy.sh        # 再次执行，真正启动
```

### 3. 扫码登录（关键步骤）

本机（你的电脑）执行端口转发：

```bash
ssh -L 6099:127.0.0.1:6099 root@<服务器IP>
```

浏览器打开 <http://127.0.0.1:6099/webui>，输入 `.env` 里设置的
`NAPCAT_WEBUI_TOKEN`，然后**扫码登录 QQ**（建议用专门的小号）。

### 4. 配置 OneBot WS 连接

NapCat WebUI →「网络配置」→ 新建正向 WebSocket：

- 地址：`0.0.0.0`，端口：`3001`（与 compose 一致）
- 若设置 token，同步填到服务器 `/opt/qqbot/.env` 的 `NAPCAT_WS_TOKEN`，然后
  `docker compose up -d` 重启生效

### 5. 验证

```bash
docker compose ps                 # 两个容器都 Up
docker compose logs -f bot        # 看到连接成功的日志
```

在群里发一个 B站链接 / b23.tv 短链，机器人应回复视频信息 + 封面。
同一会话 30 秒内只解析一次（防刷）。

## 日常运维

```bash
docker compose logs -f napcat   # NapCat 日志（登录/风控提示）
docker compose logs -f bot      # 机器人日志
docker compose restart bot      # 改插件代码后重启（需 docker compose build 重新构建）
git pull                        # 拉取本仓库更新
bash deploy.sh                  # 重新部署
```

## 常见问题

- **登录提示风控 / 设备锁**：用 QQ 小号，先在手机 QQ 上登录该号几天养号，
  再扫码登录 NapCat；登录态保存在 `napcat/qq` 目录（已 gitignore）
- **封号风险**：非官方协议有封号可能，务必使用小号，机器人行为低调
- **镜像拉不下来**：配置 Docker registry 镜像加速（见上）
- **WebUI 打不开**：确认 ssh 隧道已建立、`.env` 中 token 与 WebUI 输入一致

## 端口清单

| 端口 | 用途 | 监听地址 |
|---|---|---|
| 6099 | NapCat WebUI（登录/管理） | 127.0.0.1（ssh 隧道访问） |
| 3001 | OneBot 11 正向 WS | 127.0.0.1（bot 内网直连） |
| —    | bot 容器 | 无任何端口映射 |
