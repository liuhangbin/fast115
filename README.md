## Fast 115

Fast 115 主要目的是支持115 webdev和视频302服务, 方便视频刮削浏览。

此项目是基于 [python-115](https://github.com/ChenyangGao/web-mount-packs/tree/main/python-115-client)
包装成docker的。感谢 `python-115` 作者的支持。

代码及功能讨论请进 [Fast115群](https://t.me/fast_115), 也建议加入[115操作交流群](https://t.me/operate115)。

本人不懂前后端，python不熟悉，代码全靠抄。欢迎其他大佬贡献此项目。

感觉项目太简陋的欢迎使用[虎大的could-media-sync](https://hub.docker.com/r/imaliang/cloud-media-sync),
[小星的strm-p115](https://hub.docker.com/r/lifj25/strm-p115), [猫大的media302](https://hub.docker.com/r/ilovn/media302)

### 功能列表

- [x] 下载指定文件夹的图片，info文件并创建视频strm文件
- [x] 本地302服务
- [ ] 在线扫码二维码登陆
- [ ] 提升性能
- [ ] 上传功能
- [ ] 使用异步重构代码
- [ ] webdav 支持
- [ ] emby反向代理支持
- [x] 浏览器直接浏览已下载内容
- [ ] 更好的界面
- [ ] 更好的日志输出

### 用法

docker compose:
---
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
            - STRM_HOST=you_external_domain # strm 地址，从内部访问就内网IP, 外部访问就填外网域名
            - APP_PORT=5000 # docker映射端口，默认5000, 如果要改成其他值，请和上面的ports映射同时修改
            - SYNC_CRON="0 0 * * *" # cron 格式的定时任务
            - USERNAME=admin    # 用户名，不需要的话可以不加
            - PASSWORD=fast115  # 同上
        networks: bridge
        restart: unless-stop
```

Emby usage:
---
下面以emby docker 和 fast115 docker 为例:
```
 +---------------------------------+
 |   +----------+    +---------+   |
 |   |          |    |         |   |
 |   |   Emby   |    | Fast115 |   |
 |   |          |    |         |   |
 |   +----------+    +---------+   |    NAS Server
 |       8080           8000       |
 |                                 |   192.168.1.100
 |           +---------+           |
 |           |  Nginx  |           |
 |           |   or    |  9090     |
 |           |  Lucky  |           |
 |           +---------+           |
 +---------------------------------+

        Domain: my_domain.com
```

1. 局域网方案

局域网中，访问`emby`的地址为 `http://192.168.1.100:8080`, 访问`fast115`
的地址为 `http://192.168.1.100:8000`. 则此时 `STRM_HOST` 应填
`http://192.168.1.100:8000`.

2. 外网方案

外网访问的时候，因为经过`Nginx`或者`Lucky`反代，假设emby的访问地址为
`https://emby.my_domain.com:9090`, `fast115`的访问地址为
`https://115.my_domain.com:9090`. 则此时 `STRM_HOST` 应填
`https://115.my_domain.com:9090`.

### 注意事项

1. 使用p115拉取文件会给文件打`星标`, 在意这一点的朋友请避免使用。
2. 在有115 cookies 的时候删除 115-cookies.txt 会导致client重新请求二维码，
   从而导致服务器错误，重启容器可解决这个问题。

### 打赏

欢迎打赏 `python-115` 作者 | 如果觉得我的项目对您有帮助，也欢迎打赏我哈
--- | ---
![python-115](app/static/images/p115.jpeg) | ![Leo](app/static/images/Leo.png)
