# docker build -t fast115 -f Dockerfile .
# 使用官方 ubuntu 镜像
FROM ubuntu:24.04

LABEL org.opencontainers.image.authors="Hangbin Liu <liuhangbin@gmail.com>"

# 安装 Python 包依赖
RUN apt-get update && apt-get install -y python3 python3-pip python3-venv language-pack-zh-hans vim && apt-get autoclean
ENV LANG=zh_CN.UTF-8

# 设置工作目录
RUN mkdir /data
COPY app /app
WORKDIR /app

# Install requirements
RUN python3 -m venv /app/myenv && . /app/myenv/bin/activate && \
	pip install --no-cache-dir -r /app/requirements.txt

CMD ["/app/start.sh"]
