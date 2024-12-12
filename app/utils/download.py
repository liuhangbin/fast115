#!/usr/bin/env python3
# vim: sts=4 ts=4 sw=4 expandtab :

from p115client import P115Client
from p115client.tool.iterdir import iter_files, get_path_to_cid
from pathlib import Path
from os import makedirs, remove
from os.path import dirname, join, exists
from concurrent.futures import ThreadPoolExecutor
from urllib.request import urlopen, Request
from shutil import copyfileobj
from threading import Lock
import logging
import os, re, sys, time, json
import yaml
import sqlite3

# import utils/updatedb.py
from utils.updatedb import updatedb

from dotenv import load_dotenv
load_dotenv()

strm_dir = os.getenv('STRM_DIR', '/media')
strm_host = os.getenv('STRM_HOST', 'http://127.0.0.1:55000')
db_file = os.getenv('DB_FILE_PATH', '/data/fast115.sqlite')
sync_file = Path(os.getenv('SYNC_FILE_PATH', '/data/sync.yaml')).expanduser()
VIDEO_EXTENSIONS = [
    ".3gp", ".amv", ".asf", ".divx", ".dv", ".drc", ".flv", ".f4v", ".h264",
    ".iso", ".ivf", ".m2ts", ".m4v", ".mkv", ".mov", ".mp4", ".mpg", ".mpeg",
    ".mpv", ".rm", ".rmvb", ".svi", ".ts", ".vob", ".webm", ".wmv", ".xvid",
    ".yuv", ".qt", ".ogv", ".mxf", ".avi"
]

# 下载文件的通用函数
def download_file(client, pickcode: str, file_path: str, overwrite: bool) -> bool:
    if os.path.exists(file_path) and not overwrite:
        logging.info(f"跳过已存在的文件: {file_path}")
        return False

    # 检查是不是url
    if pickcode.find("115.com") != -1:
        url = pickcode
    else:
        url = client.download_url(pickcode)
    try:
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with urlopen(Request(url, headers=url["headers"])) as response, open(file_path, "wb") as f:
            copyfileobj(response, f)
        logging.info(f"文件下载完成: {file_path}")
        return True
    except Exception as e:
        logging.error(f"下载文件 {file_path} 失败: {e}")
        if os.path.exists(file_path):
            os.remove(file_path)
        return False

# 下载元数据文件的函数
def download_metadata(client, attr, download_dir: str, overwrite: bool, allowed_extensions: tuple) -> bool:
    file_name = attr.get('name')
    file_path = os.path.join(download_dir, attr["path"].lstrip("/"))

    if not file_name.endswith(allowed_extensions):
        return False

    return download_file(client, attr["pickcode"], file_path, overwrite)

def download_pic(attr):
    # 替换缩略图的路径，并下载图片
    thumb = attr["thumb"].replace("_100?", "_0?")
    img_path = strm_dir + attr["path"]

    download_file(client, thumb, img_path, False)

def insert_strm(name, pickcode, strm_path):
    if os.path.exists(strm_path):
        logging.info(f"跳过已存在的 .strm 文件: {strm_path}")
        return

    # 创建 translate 方法
    transtab = {c: f"%{c:02x}" for c in b"/%?#"}
    translate = str.translate

    try:
        os.makedirs(os.path.dirname(strm_path), exist_ok=True)
        with open(strm_path, "w") as f:
            f.write(f"{strm_host}/{translate(name, transtab)}?pickcode={pickcode}")
        logging.info(f"生成 .strm 文件: {strm_path}")
    except Exception as e:
        logging.error(f"写入 .strm 文件时出错: {e}")

def create_strm_from_data(dir_path):
    conn = sqlite3.connect(db_file)
    if not conn:
        logging.error("无法连接到数据库")

    # 匹配路径的时候最后添加/， 以免路径前面相同
    dir_path = dir_path.rstrip('/') + '/'
    cursor = conn.cursor()
    cursor.execute(f"SELECT name, pickcode, path FROM data WHERE path LIKE '{dir_path}%';")
    data = cursor.fetchall()
    conn.close()

    logging.info("开始遍历文件并生成 .strm 文件...")
    for name, pickcode, path in data:
        if Path(path).suffix.lower() in VIDEO_EXTENSIONS:
            # 分离文件名和扩展名
            file_path, _ = os.path.splitext(path)
            # 拼接路径，确保路径格式正确
            strm_path = strm_dir + file_path + ".strm"
            insert_strm(name, pickcode, strm_path)

def download_files(client, cid, filetype, filepath):
    logging.info(f"过滤文件类型: {filetype}")
    if filetype['video']:
        create_strm_from_data(filepath)
    if filetype['image']:
        logging.info("开始使用多线程下载图片...")
        with ThreadPoolExecutor(20) as executor:
            executor.map(lambda attr: download_pic(attr), iter_files(client, cid, type=2, with_path=True))
    # 遍历文件并下载元数据和字幕文件
    extensions=[]
    if filetype['nfo']:
        extensions.append('.nfo')
    if filetype['subtitle']:
        extensions.extend(['.srt', '.ass', '.ssa'])
    if len(extensions) > 0:
        logging.info("开始遍历文件并下载字幕元数据...")
        for attr in iter_files(client, cid, type=99, with_path=True):
            download_metadata(client, attr, strm_dir, False, tuple(extensions))

def delete_file(file):
    logging.info(f"删除文件: {file}")
    Path(file).unlink(missing_ok=True)

def deal_with_action(client, sync_path, attr, action, old_attr=None, summary=None):
    if not attr['path'].startswith(sync_path['path']):
        return

    logging.info(f"增量更新: {summary}")

    ext = Path(attr['path']).suffix.lower()
    file_type = sync_path['filetype']

    def handle_file(client, pickcode, path, old_path, action):
        """通用文件处理函数"""
        if action == 'delete':
            delete_file(path)
        elif action == 'insert':
            download_file(client, pickcode, path, False)
        elif action == 'update' and (summary.get('move') or summary.get('rename')):
            delete_file(old_path)
            download_file(client, pickcode, path, False)

    if 'video' in file_type and ext in VIDEO_EXTENSIONS:
        # deal with videos
        file_path, _ = os.path.splitext(attr['path'])
        strm_path = strm_dir + file_path + ".strm"
        if action == 'delete':
            delete_file(strm_path)
        elif action == 'insert':
            insert_strm(attr["name"], attr["pickcode"], strm_path)
        elif action == 'update' and (summary['move'] or summary['rename']):
            old_file_path, _ = os.path.splitext(old_attr['path'])
            old_strm_path = strm_dir + old_file_path + ".strm"
            delete_file(old_strm_path)
            insert_strm(attr["name"], attr["pickcode"], strm_path)
    elif (
        ('image' in file_type and attr['is_image']) or
        ('nfo' in file_type and ext == '.nfo') or
        ('subtitle' in file_type and ext in ['.srt', '.ass', '.ssa'])
    ):
        # deal with nfo and subtitles
        file_path = strm_dir + attr["path"]
        old_path = strm_dir + old_attr["path"] if old_attr else None
        handle_file(client, attr["pickcode"], file_path, old_path, action)

def sync_path(client, path, data):
    for _id, _type, _old, _new, _summary, time in data:
        old = json.loads(_old) if _old else None
        new = json.loads(_new) if _new else None
        summary = json.loads(_summary) if _summary else None
        if _type == 'insert':
            deal_with_action(client, path, new, _type, summary=summary)
        elif _type == 'delete':
            deal_with_action(client, path, old, _type, summary=summary)
        elif _type == 'update':
            deal_with_action(client, path, new, _type, old_attr=old, summary=summary)

# 增量更新
def sync_from_now(client, use_fuse = False):
    conn = sqlite3.connect(db_file)
    if not conn:
        logging.error("无法连接到数据库")
        return 1

    # 清理事件数据库
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM event;")
        conn.commit()
    except sqlite3.OperationalError as e:
        logging.error(f"Error: {e}")
        return 1

    files = {}
    if os.path.exists(sync_file):
        with open(sync_file, 'r') as fp:
            files = yaml.safe_load(fp) or {}  # 确保文件为空时返回空字典
            file_list = list(files.keys())
            if len(file_list) > 0:
                updatedb(client, dbfile = conn, top_dirs = file_list, clean = True)

    if use_fuse:
        conn.close()
        return 0

    cursor.execute("SELECT * FROM event;")
    data = cursor.fetchall()
    conn.close()

    for f in files:
        sync_path(client, files[f], data)

    return 0

# 全量更新: 暂时跳过已存在文件，如其他人有需求再添加强制覆盖选项
def sync_from_beginning(client, use_fuse = False):
    start_time = time.time()
    files = {}
    if os.path.exists(sync_file):
        with open(sync_file, 'r') as fp:
            files = yaml.safe_load(fp) or {}  # 确保文件为空时返回空字典
            file_list = list(files.keys())
            if len(file_list) > 0:
                updatedb(client, dbfile = db_file, top_dirs = file_list, clean = True)

    if not use_fuse:
        for cid in files:
            download_files(client, cid, files[cid]['filetype'], files[cid]['path'])

    end_time = time.time()
    total_time = end_time - start_time
    logging.info(f"总共耗时: {total_time:.2f} 秒")

def download_path(client, path, filetype, use_fuse = False):
    logging.info(f"使用自定义保存路径: {strm_dir}")
    makedirs(strm_dir, exist_ok=True)
    cid = 0

    # Check if the path is cid directly
    if path.isdigit():
        cid = path
    else:
        # Check if the path is a URL, e.g. https://115.com/?cid=0&offset=0&tab=&mode=wangpan
        match = re.search(r"\?cid=([0-9]+)", path)
        if match:
            cid = match.group(1)
        else:
            # 将 path 作为目录处理，尝试获取 cid
            response = client.fs_dir_getid(path)
            if response['errno'] != 0:
                logging.error(f"路径获取 cid 失败: {response['error']}")
                return
            else:
                cid = response['id']

    # Don't know why the cid is str, need to convert it to int when get path
    path = get_path_to_cid(client, int(cid))

    # 保存同步目录
    if exists(sync_file):
        with open(sync_file, 'r', encoding='utf-8') as fp:
            files = yaml.safe_load(fp) or {}  # 确保文件为空时返回空字典

        files[cid] = {'path': path, 'filetype': filetype}

        with open(sync_file, 'w', encoding='utf-8') as fp:
            yaml.dump(files, fp, allow_unicode=True)
    else:
        with open(sync_file, 'w', encoding='utf-8') as fp:
            yaml.dump({cid: {'path': path, 'filetype': filetype}}, fp, allow_unicode=True)

    start_time = time.time()

    logging.info(f"开始更新数据库文件")
    updatedb(client, dbfile = db_file, top_dirs = cid, clean = True)
    if not use_fuse:
        download_files(client, cid, filetype, path)

    end_time = time.time()
    total_time = end_time - start_time
    logging.info(f"总共耗时: {total_time:.2f} 秒")
