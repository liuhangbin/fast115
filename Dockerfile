# docker build -t fast115 -f Dockerfile .
# 使用官方 ubuntu 镜像
FROM ubuntu:24.04

LABEL org.opencontainers.image.authors="Hangbin Liu <liuhangbin@gmail.com>"

# 安装 Python 包依赖
RUN apt-get update && apt-get install -y libfuse2 fuse python3 python3-pip \
	python3-venv language-pack-zh-hans vim && \
	apt-get autoclean
RUN sed -i 's/# user_allow_other/user_allow_other/' /etc/fuse.conf
ENV LANG=zh_CN.UTF-8

# Install requirements
RUN python3 -m venv /myenv
# basic packages
RUN . /myenv/bin/activate && pip install fusepy python-dotenv pyyaml requests urllib3
RUN . /myenv/bin/activate && pip install apscheduler croniter posixpatht
# Web framework
RUN . /myenv/bin/activate && pip install flask flask_login
RUN . /myenv/bin/activate && pip install blacksheep uvicorn WsgiDAV
# p115 requires
RUN . /myenv/bin/activate && pip install cachedict ed2k path_predicate qrcode python-emby-proxy
# always use the latest p115client
RUN . /myenv/bin/activate && pip install --no-cache-dir p115client p115servedb p115updatedb python-emby-proxy
RUN . /myenv/bin/activate && pip cache purge

# 设置工作目录
RUN mkdir /data
COPY app /app
WORKDIR /app

CMD ["/app/start.sh"]
