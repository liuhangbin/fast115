# docker build -t fast115 -f Dockerfile .
# 使用官方 ubuntu 镜像
FROM ubuntu:24.04

LABEL org.opencontainers.image.authors="Hangbin Liu <liuhangbin@gmail.com>"

# 安装 Python 包依赖
RUN apt-get update && apt-get install -y python3 python3-pip python3-venv language-pack-zh-hans vim && apt-get autoclean
ENV LANG=zh_CN.UTF-8

# Install requirements
RUN python3 -m venv /myenv && . /myenv/bin/activate && \
	pip install --no-cache-dir flask flask_login p115client python-dotenv pyyaml requests urllib3
RUN . /myenv/bin/activate && pip install --no-cache-dir apscheduler croniter posixpatht

# 设置工作目录
RUN mkdir /data
COPY app /app
WORKDIR /app

CMD ["/app/start.sh"]
