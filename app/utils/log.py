#!/usr/bin/env python3
# vim: sts=4 ts=4 sw=4 expandtab :

import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime, timedelta
from pathlib import Path
import os

from dotenv import load_dotenv
# load .env
load_dotenv()

log_file = Path(os.getenv('LOG_FILE_PATH', '/data/fast115.log')).expanduser()
if not os.path.exists(log_file):
    with open(log_file, 'w') as f:
        f.write('')

#确保日志大小在10m以内
def trim_log_file(log_file_path,max_log_size_bytes=10 * 1024 * 1024):
    if not os.path.exists(log_file_path):
        return
    max_log_size_bytes = 10 * 1024 * 1024  # 指定的最大日志文件大小，单位是字节
    retention_size_bytes = int(10 * 1024 * 1024 * 0.5)  # 保留的日志内容大小，单位是字节

    # 获取当前日志文件大小
    current_size = os.path.getsize(log_file_path)

    # 如果当前大小超过最大大小
    if current_size > max_log_size_bytes:
        # 读取日志文件内容
        with open(log_file_path, 'rb') as file:
            # 移动文件指针到倒数 retention_size_bytes 的位置
            file.seek(-retention_size_bytes, os.SEEK_END)
            # 读取剩余内容
            retained_content = file.read()

        # 将保留的内容写回文件
        with open(log_file_path, 'wb') as file:
            file.write(retained_content)

def print_message(message):
    timestamp = datetime.now().strftime("[%Y/%m/%d %H:%M:%S]")
    trim_log_file(log_file)
    logging.info(message)

def configure_logging(log_file=log_file, max_log_size_bytes=10 * 1024 * 1024, date_format='%Y-%m-%d %H:%M:%S'):
    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
    )

    # 创建 RotatingFileHandler，设置日志文件大小
    handler = RotatingFileHandler(log_file, maxBytes=max_log_size_bytes)
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt=date_format))
    # 将 handler 添加到 root logger
    logging.getLogger('').addHandler(handler)
    # 禁用 Flask Werkzeug 日志
    logging.getLogger('werkzeug').setLevel(logging.ERROR)

def read_log_file():
    try:
        with open(log_file, "r", encoding="utf-8") as file:
            content = file.read()
    except Exception as e:
        content = f"Error reading log file: {str(e)}"

    return content
