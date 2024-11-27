#!/bin/bash

# 镜像的版本号和 Docker Hub 用户名
VERSION="0.1.5"
IMAGE_NAME="liuhangbin/fast115"
PLATFORMS="linux/amd64,linux/arm64"

# 启用 buildx 构建器
docker run --rm --privileged multiarch/qemu-user-static --reset -p yes

# 检查是否已有构建器，如果没有则创建一个新的
if ! docker buildx ls | grep -q 'fast115_builder'; then
    docker buildx create --name fast115_builder
fi

# 构建并推送多架构镜像
docker buildx build --builder fast115_builder --platform $PLATFORMS \
    -t $IMAGE_NAME:$VERSION \
    -t $IMAGE_NAME:latest \
    --push .

# 检查是否成功推送
if [ $? -eq 0 ]; then
    echo "Docker 镜像构建并推送成功: $IMAGE_NAME:$VERSION 和 $IMAGE_NAME:latest"
else
    echo "Docker 镜像构建或推送失败"
    exit 1
fi
