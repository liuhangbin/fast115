## Fast 115

`Fast 115` 主要目的是支持115 webdev和视频302服务, 方便视频刮削浏览。

此项目是基于 [p115 client](https://github.com/ChenyangGao/p115client.git)。感谢 `p115 client` 作者的支持。

此项目代码及功能讨论请进 [Fast115群](https://t.me/fast_115), 底层操作或者咨询其他类似项目请加入[115操作交流群](https://t.me/operate115)。

本人不懂前后端，python不熟悉，代码全靠抄。欢迎大家贡献此项目。

### 功能列表

- [x] 下载指定文件夹的图片，info文件并创建视频strm文件
- [x] 浏览器直接浏览已下载内容
- [ ] 单独删除某一个同步目录
- [x] 在线扫码二维码登陆
- [x] 本地302服务
- [x] 登陆验证
- [x] 增量同步
- [ ] 上传功能
- [ ] webdav 支持
- [ ] emby客户端302支持
- [ ] 提升性能
- [ ] 更好的界面
- [ ] 更好的日志输出
- [ ] 使用异步重构代码

### 网站样式

![Home](app/static/images/home.png)
![File](app/static/images/file.png)
![Sync](app/static/images/sync.png)
![Logs](app/static/images/log.png)

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

### 打赏

欢迎打赏 `p115 client` 作者 | 如果觉得我的项目对您有帮助，也欢迎打赏我哈
--- | ---
![p115 client](app/static/images/p115.jpeg) | ![Leo](app/static/images/Leo.png)
