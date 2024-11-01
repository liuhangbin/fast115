#!/usr/bin/env python3
# vim: sts=4 ts=4 sw=4 expandtab :

from p115client import P115Client
from pathlib import Path
from os import makedirs, remove
from os.path import dirname, join, exists
from p115client.tool import iter_files
from p115client.tool.download import iter_subtitles_with_url
from concurrent.futures import ThreadPoolExecutor
from urllib.request import urlopen, Request
from shutil import copyfileobj
from threading import Lock
import logging
import os
import sys
import time

from dotenv import load_dotenv
load_dotenv()
strm_dir = os.getenv('STRM_DIR', '/media')
strm_host = os.getenv('STRM_HOST', 'http://127.0.0.1:55000')

lock = Lock()

def download_pic(attr, count):
    # 替换缩略图的路径，并下载图片
    thumb = attr["thumb"].replace("_100?", "_0?")
    img_path = strm_dir + attr["path"]

    # 跳过已存在的图片文件
    if exists(img_path):
        logging.info(f"跳过已存在的图片文件: {img_path}")
        with lock:
            count['existing_image_count'] += 1
        return

    try:
        os.makedirs(dirname(img_path), exist_ok=True)  # 确保目录存在

        # 打开 URL 进行下载
        with urlopen(thumb) as response, open(img_path, "wb") as f:  # 使用 with 确保文件会被关闭
            downloaded = 0

            # 逐块下载文件并输出进度
            buffer_size = 1024  # 每次读取的字节数
            while True:
                buffer = response.read(buffer_size)
                if not buffer:
                    break
                f.write(buffer)
                downloaded += len(buffer)

        with lock:
            count['image_count'] += 1
        logging.info(f"下载图片完成: {img_path}")
    except Exception as e:
        with lock:
            count['failed_download_count'] += 1
        logging.error(f"下载图片失败: {e}")

def download_pictures(client, cid, count):
    # 使用多线程下载图片
    logging.info("开始使用多线程下载图片...")
    with ThreadPoolExecutor(20) as executor:
        executor.map(lambda attr: download_pic(attr, count), iter_files(client, cid, type=2, with_path=True))

def download_file(client, attr, count):
    file_name = attr.get('name')
    file_path = strm_dir + attr["path"]

    # 定义元数据文件扩展名
    metadata_extensions = ['.nfo', '.srt', '.ass', '.ssa']
    #logging.info(f"定义元数据文件扩展名: {metadata_extensions}")

    # 检查文件扩展名是否在 metadata_extensions 列表中
    if not any(file_name.endswith(ext) for ext in metadata_extensions):
        #logging.info(f"跳过下载: {file_name} 不符合扩展名要求")
        return

    # 检查文件是否已经存在
    if exists(file_path):
        logging.info(f"跳过下载: {file_name} 已存在")
        count['existing_metadata_count'] += 1
        return

    url = client.download_url(attr["pickcode"], use_web_api=True)
    logging.info(f"准备下载文件: {file_name} 到 {file_path}")
    try:
        if not exists(dirname(file_path)):
            logging.info(f"创建目录: {dirname(file_path)}")
            makedirs(dirname(file_path))
        with open(file_path, "wb") as f:
            logging.info(f"开始下载文件: {file_name}")
            file = urlopen(Request(url, headers=url["headers"]))
            copyfileobj(file, f)
            logging.info(f"文件下载完成: {file_name}")
        count['metadata_count'] += 1
    except Exception as e:
        logging.error(f"下载文件 {file_name} 失败: {e}")
        # 删除可能生成的空文件
        if exists(file_path):
            logging.info(f"删除空文件: {file_name}")
            remove(file_path)

def download_nfo(client, cid, count):
    # 遍历文件并下载
    logging.info("开始遍历文件并下载元数据...")
    for attr in iter_files(client, cid, type=99, with_path=True):
        download_file(client, attr, count)

def download_subtitle(client, cid, count):
    # 遍历文件并下载
    logging.info("开始遍历文件并下载字幕...")
    for attr in iter_subtitles_with_url(client, cid, with_path=True):
        download_file(client, attr, count)

def create_strm(client, cid, count):
    # 创建 translate 方法
    transtab = {c: f"%{c:02x}" for c in b"/%?#"}
    translate = str.translate

    # 遍历文件并生成 .strm 文件
    logging.info("开始遍历文件并生成 .strm 文件...")
    for attr in iter_files(client, cid, type=4, with_path=True):
        # 分离文件名和扩展名
        file_path, _ = os.path.splitext(attr["path"])
        # 拼接路径，确保路径格式正确
        strm_path = strm_dir + file_path + ".strm"

        # 跳过已存在的 .strm 文件
        if exists(strm_path):
            logging.info(f"跳过已存在的 .strm 文件: {strm_path}")
            count['existing_strm_count'] += 1
            continue

        try:
            os.makedirs(dirname(strm_path), exist_ok=True)  # 确保目录存在
            with open(strm_path, "w") as f:  # 使用 with 确保文件会被关闭
                f.write(f"{strm_host}/{translate(attr['name'], transtab)}?pickcode={attr['pickcode']}")
            count['strm_count'] += 1
            logging.info(f"生成 .strm 文件: {strm_path}")
        except Exception as e:
            logging.error(f"写入 .strm 文件时出错: {e}")

def download_path(client, path):
    logging.info(f"使用自定义保存路径: {strm_dir}")
    makedirs(strm_dir, exist_ok=True)
    cid = 0

    response = client.fs_dir_getid(path)
    if response['errno'] != 0:
        logging.error(f"路径获取cid失败: {response['error']}")
        return
    else:
        cid = response['id']

    # 统计变量
    count = {'strm_count': 0, 'existing_strm_count': 0,
             'image_count': 0, 'existing_image_count': 0,
             'metadata_count': 0, 'existing_metadata_count': 0,
             'failed_download_count': 0}

    # 开始时间
    start_time = time.time()

    create_strm(client, cid, count)
    download_pictures(client, cid, count)
    download_nfo(client, cid, count)
    download_subtitle(client, cid, count)

    # 结束时间
    end_time = time.time()

    # 计算总时间
    total_time = end_time - start_time

    # 输出统计结果
    total_files = sum(count.values())

    logging.info(f"总共生成新的 .strm 文件: {count['strm_count']}")
    logging.info(f"总共跳过已存在的 .strm 文件: {count['existing_strm_count']}")
    logging.info(f"总共下载新的图片: {count['image_count']}")
    logging.info(f"总共跳过已存在的图片: {count['existing_image_count']}")
    logging.info(f"总共下载新的元数据文件: {count['metadata_count']}")
    logging.info(f"总共跳过已存在的元数据文件: {count['existing_metadata_count']}")
    logging.info(f"总共下载失败: {count['failed_download_count']}")
    logging.info(f"总共处理文件: {total_files}")
    logging.info(f"总共耗时: {total_time:.2f} 秒")
