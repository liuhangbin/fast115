#!/usr/bin/env python3
# vim: sts=4 ts=4 sw=4 expandtab :

from flask import Flask, request, Response, flash
from flask import redirect, url_for, render_template, send_from_directory
from p115client import P115Client
from p115client.tool.iterdir import iter_files
from pathlib import Path
import os

from utils.log import print_message, configure_logging, read_log_file
from utils.download import download_path
from utils.web302 import find_query_value, get_downurl, get_pickcode_for_sha1

app = Flask(__name__)
strm_dir = os.getenv('STRM_DIR', '/media')
cookies_path = Path(os.getenv('COOKIE_PATH', '/data/115-cookies.txt')).expanduser()
if not os.path.exists(cookies_path):
    with open(cookies_path, 'w') as f:
        f.write('')

@app.errorhandler(404)  # 传入要处理的错误代码
def page_not_found(e):  # 接受异常对象作为参数
    return render_template('404.html'), 404  # 返回模板和状态码

@app.route('/', methods=['GET', 'POST'])
@app.route('/index.html', methods=['GET', 'POST'])
def index():
    # 读取 cookies 文件
    client = P115Client(cookies_path)
    if not client.login_status():
        return redirect(url_for('login'))  # 跳转到登录页面

    if request.method == 'POST':
        path = request.form.get('path')
        #create_strm = 'create_strm' in request.form  # 选择框的状态
        download_path(client, path)
        return redirect(url_for('index'))  # 重定向回主页

    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
@app.route('/login.html', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        # 获取表单数据
        cookies = request.form.get('cookies')
        app_name = request.form.get('app')

        if cookies and app_name:  # 确保都获取到
            client = P115Client(cookies=cookies, app=app_name)
            if client.login_status():
                open(cookies_path, "w").write(client.cookies_str)
                return redirect(url_for('index'))  # 登录成功，重定向回主页
            else:
                print_message(f'Login failed: invalid cookies?')
                return render_template('login.html')
        else:
            flash('cookies and app are required!')

    return render_template('login.html')

@app.get("/log")
@app.get("/log.html")
def log_view():
    return render_template('log.html')

@app.route('/log_data')
def log_data():
    return Response(read_log_file(), content_type='text/plain')

@app.route('/file/')
@app.route('/file/<path:subpath>')
def file_browser(subpath=''):
    current_path = os.path.join(strm_dir, subpath)
    try:
        items = os.listdir(current_path)
    except FileNotFoundError:
        items = []

    items_with_path = []
    for item in items:
        item_path = os.path.join(subpath, item)
        full_item_path = os.path.join(current_path, item)  # 获取完整路径
        relative_path = ""

        if os.path.isdir(full_item_path):
            item_path += '/'  # 对于文件夹，添加斜杠
        else:
            relative_path = os.path.relpath(full_item_path, strm_dir)

        items_with_path.append((item, item_path, relative_path))

    return render_template('file.html', items=items_with_path, current_path=subpath)

@app.route('/download/<path:filename>')
def download_file(filename):
    full_path = os.path.join(strm_dir, filename)
    if not os.path.isfile(full_path):
        return f"File not found: {full_path}", 404  # 返回自定义的404信息

    return send_from_directory(strm_dir, filename, as_attachment=True)

@app.route("/<path:name>", methods=["GET", "HEAD"])
def web302(name=""):
    if not os.path.exists(cookies_path):
        return render_template('404.html', error = f"no cookie file found: {cookies_path}"), 404
    cookies = open(cookies_path, encoding="latin-1").read()
    query_string = request.query_string.decode().strip()
    pickcode = find_query_value(query_string, "pickcode")
    if not pickcode:
        sha1 = find_query_value(query_string, "sha1")
        if sha1:
            if sha1.strip(hexdigits):
                return Response(f"bad sha1: {sha1!r}", 400)
        elif len(query_string) == 40 and not query_string.strip(hexdigits):
            sha1 = query_string
        if sha1:
            pickcode = get_pickcode_for_sha1(sha1.upper())
            if not pickcode:
                return render_template('404.html', error = f"no file with sha1: {sha1!r}"), 404
    if not pickcode:
        pickcode = query_string
    if not pickcode.isalnum():
        return Response(f"bad pickcode: {pickcode!r}", 400)
    user_agent = request.headers.get("user-agent", "")
    resp = get_downurl(cookies, pickcode.lower(), user_agent)
    if resp["state"]:
        item = next(iter(resp["data"].values()))
        if item["url"]:
            return redirect(item["url"]["url"])
    return render_template('404.html', error = f"no file with pickcode: {pickcode!r}"), 404  # 返回模板和状态码

if __name__ == '__main__':
    configure_logging()
    app.run(host='0.0.0.0', port=5000)
