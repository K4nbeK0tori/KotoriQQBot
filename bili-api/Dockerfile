# B站视频下载 API 服务（第三方项目：https://github.com/Suxiaoqinx/bilibili）
# 通过抓取视频页 __playinfo__ 获取流地址，绕开 playurl API 风控。
FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg git \
    && rm -rf /var/lib/apt/lists/*

# 构建时拉取第三方代码（服务器可访问 GitHub）
RUN git clone --depth 1 https://github.com/Suxiaoqinx/bilibili.git /app \
    && rm -rf /app/.git

RUN pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 可选：挂载 cookies.txt 提升下载稳定性/清晰度（用其 bililogin.py 扫码生成）
# 见 docker-compose.yml 中 bili-api 的 volumes 注释

CMD ["uvicorn", "fastapi_app:app", "--host", "0.0.0.0", "--port", "8000"]
