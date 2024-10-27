## Fast 115

Fast 115 主要目的是支持115 webdev和视频302服务, 方便视频刮削浏览。

此项目是基于 [p115 client](https://github.com/ChenyangGao/p115client.git)
包装成docker的。感谢 `p115 client` 作者的支持。

代码及功能讨论请进群 [TG 群](https://t.me/operate115)

本人不懂前后端，python不熟悉，代码全靠抄。欢迎其他大佬贡献此项目。

感觉项目太简陋的欢迎使用[小星的收费版](https://hub.docker.com/r/lifj25/strm-p115)

### 功能列表

- [x] 下载指定文件夹的图片，info文件并创建视频strm文件
- [x] 本地302服务
- [ ] 在线扫码二维码登陆
- [ ] 提升性能
- [ ] 上传功能
- [ ] 使用异步重构代码
- [ ] webdav 支持
- [ ] emby反向代理支持
- [ ] 浏览器直接浏览已下载内容
- [ ] 更好的界面
- [ ] 更好的日志输出

### 用法

Dockerfile
```
services:
    fast115:
        image: liuhangbin/fast115:latest
        container_name: fast115
        hostname: fast115
        ports:
            - 55000:5000
        volumes:
            - /your_data_path:/data     # 数据目录，存放 cookies, logs
            - /your_media_path:/media   # 媒体目录，存放strm 链接等
        environment:
            - TZ=Asia/Shanghai
            - STRM_HOST=host.docker.internal # strm 链接host地址，从内部访问就 0.0.0.0, 外部访问就填 host ip
            - STRM_PORT=55000     # strm 链接端口
        extra_hosts:
            - "host.docker.internal:host-gateway"   # docker 内部访问host的地址，建议emby docker 也加上这个
        networks: bridge
        restart: unless-stop
```

### 注意事项

1. 使用p115拉取文件会给文件打`星标`, 在意这一点的朋友请避免使用。
2. 在有115 cookies 的时候删除 115-cookies.txt 会导致client重新请求二维码，
   从而导致服务器错误，重启容器可解决这个问题。

### 打赏

欢迎打赏 `p115 client` 作者 | 如果觉得我的项目对您有帮助，也欢迎打赏我哈
--- | ---
![p115client](app/static/images/p115clint.jpeg) | ![Leo](app/static/images/Leo.png)
