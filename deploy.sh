#!/usr/bin/env bash
# =============================================================
# 服务器一键部署脚本（在 Debian 13 上执行）
#
# 用法（首次，从 GitHub 拉代码）：
#   bash deploy.sh https://github.com/<你>/<仓库>.git
# 之后更新代码再部署：
#   git pull && bash deploy.sh
# =============================================================
set -euo pipefail

REPO_URL="${1:-}"

echo "==> [0/4] 检查环境"
if ! command -v docker >/dev/null 2>&1; then
  echo "未安装 Docker，执行：curl -fsSL https://get.docker.com | sh"
  exit 1
fi
docker compose version >/dev/null 2>&1 || {
  echo "缺少 docker compose 插件，请安装：apt install docker-compose-plugin"
  exit 1
}

# 首次部署：从 GitHub 克隆
if [ ! -f docker-compose.yml ]; then
  if [ -z "$REPO_URL" ]; then
    echo "当前目录没有 docker-compose.yml，请提供仓库地址：bash deploy.sh <repo-url>"
    exit 1
  fi
  echo "==> 从 GitHub 克隆仓库"
  git clone "$REPO_URL" .
fi

# 生成 .env
if [ ! -f .env ]; then
  echo "==> 生成 .env 模板"
  cp .env.example .env
  echo
  echo "!!! 请先编辑 .env："
  echo "      vi .env"
  echo "    - NAPCAT_UID / NAPCAT_GID 改为你的 uid（id -u / id -g）"
  echo "    - NAPCAT_WEBUI_TOKEN 改成随机字符串"
  echo "    改完再执行：bash deploy.sh"
  exit 0
fi

echo "==> [1/4] 准备 NapCat 挂载目录"
mkdir -p napcat/config napcat/qq

echo "==> [2/4] 构建并启动容器"
docker compose up -d --build

echo "==> [3/4] 查看状态"
docker compose ps

echo "==> [4/4] 完成"
echo
echo "下一步（本机执行，把 <服务器IP> 换成你的服务器）："
echo "  ssh -L 6099:127.0.0.1:6099 root@<服务器IP>"
echo "然后浏览器打开 http://127.0.0.1:6099/webui 扫码登录并配置"
echo
echo "常用命令："
echo "  docker compose logs -f bot      # 看机器人日志"
echo "  docker compose logs -f napcat   # 看 NapCat 日志"
echo "  docker compose restart bot      # 重启机器人"
