#!/usr/bin/env python3
# vim: sts=4 ts=4 sw=4 expandtab :

from flask import Flask, request, Response, flash, jsonify
from flask import redirect, url_for, render_template, send_from_directory
from flask_login import LoginManager, login_user, login_required, logout_user, current_user, UserMixin
from p115client import P115Client
from pathlib import Path
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR
from croniter import croniter
from datetime import datetime
import os, re, yaml, json
import requests

from utils.log import print_message, configure_logging, read_log_file
from utils.download import download_path, sync_from_now, sync_from_beginning
from utils.web302 import find_query_value, get_downurl, get_pickcode_for_sha1

from dotenv import load_dotenv
# load .env
load_dotenv()

default_user = os.getenv('USERNAME', None)
default_pass = os.getenv('PASSWORD', '@#$%^&!')
strm_dir = os.getenv('STRM_DIR', '/media')
app_port = os.getenv('APP_PORT', '5000')
sync_cron = os.getenv('SYNC_CRON', '')
sync_file = Path(os.getenv('SYNC_FILE_PATH', '/data/sync.yaml')).expanduser()
cookies_path = Path(os.getenv('COOKIE_PATH', '/data/115-cookies.txt')).expanduser()
if not os.path.exists(cookies_path):
    with open(cookies_path, 'w') as f:
        f.write('')

app = Flask(__name__)
app.secret_key = os.urandom(24)
login_manager = LoginManager(app)
# 默认登陆地址
login_manager.login_view = 'login'

# User类继承自 UserMixin，这样就自动拥有了许多方法
class User(UserMixin):
    def __init__(self, username):
        self.username = username

    @property
    def id(self):
        return self.username

# Flask-Login的user_loader，在这里我们不需要查询数据库，因为我们只有一个用户
@login_manager.user_loader
def load_user(user_id):
    # 假设只有一个用户，直接返回User对象
    if user_id == default_user or user_id == 'fast115':
        return User(user_id)
    return None

# 定义你想定期执行的任务
def scheduled_task():
    client = P115Client(cookies_path)
    if not client.login_status():
        print_message(f'Unable to sync files: invalid cookies?')
        return

    print_message("开始定时任务")
    sync_from_now(client)

def validate_cron_expression(cron_expression):
    try:
        cron = croniter(cron_expression, datetime.now())
        return True
    except ValueError as e:
        print_message(f"Invalid cron expression: {e}")
        return False

def parse_cron_expression(cron_expression):
    """
    将 cron 表达式解析为字典，传递给 APScheduler add_job 方法。
    调用前需调用 validate_cron_expression() 来验证cron格式正确
    示例：
        "0 1 * * *"  -> {"minute": "0", "hour": "1"}
    """
    parts = cron_expression.split()
    cron_dict = {
        'minute': parts[0],
        'hour': parts[1],
        'day': parts[2],
        'month': parts[3],
        'day_of_week': parts[4]
    }
    return cron_dict

# 配置 APScheduler
def start_scheduler():
    # 验证cron是否正确
    if not validate_cron_expression(sync_cron):
        return
    # 创建调度器
    scheduler = BackgroundScheduler()
    # 使用 cron 表达式添加定时任务
    try:
        scheduler.add_job(scheduled_task, 'cron', **parse_cron_expression(sync_cron))
        scheduler.start()
        print_message(f"Job scheduled with cron expression: {sync_cron}")
    except ValueError as e:
        print_message(f"Error scheduling job: {e}")

@app.errorhandler(404)  # 传入要处理的错误代码
def page_not_found(e):  # 接受异常对象作为参数
    return render_template('404.html'), 404  # 返回模板和状态码

@app.route('/login', methods=['GET', 'POST'])
def login():
    if default_user is None:
        user = User('fast115')
        login_user(user)  # 无用户的时候默认登陆
        return redirect(url_for('index'))  # 重定向到主页

    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        # 验证用户名和密码是否一致
        if username == default_user and password == default_pass:
            user = User(username)
            login_user(user)  # 登入用户
            flash('Login success.')
            return redirect(url_for('index'))  # 重定向到主页

        flash('Invalid username or password.')  # 如果验证失败，显示错误消息
        return redirect(url_for('login'))  # 重定向回登录页面

    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()  # 登出用户
    flash('Goodbye.')
    return redirect(url_for('index'))  # 重定向回首页

@app.route('/', methods=['GET', 'POST'])
@app.route('/index.html', methods=['GET', 'POST'])
@login_required
def index():
    # 读取 cookies 文件
    client = P115Client(cookies_path)
    if not client.login_status():
        return redirect(url_for('cookies'))  # 跳转到登录页面

    if request.method == 'POST':
        path = request.form.get('path')
        #create_strm = 'create_strm' in request.form  # 选择框的状态
        filetype = {}
        count_true = 0
        filetype['video'] = 'video' in request.form
        filetype['image'] = 'image' in request.form
        filetype['nfo'] = 'nfo' in request.form
        filetype['subtitle'] = 'subtitle' in request.form
        for key in filetype:
            if filetype[key]:
                count_true += 1
        if count_true == 0:
            flash('至少要同步一种类型的文件')
            return redirect(url_for('index'))

        download_path(client, path, filetype)
        return redirect(url_for('index'))  # 重定向回主页

    return render_template('index.html')

@app.route('/api/token')
def get_token():
    # 获取二维码的 token（从外部API）
    qrcode_api_url = "https://qrcodeapi.115.com/api/1.0/web/1.0/token/"
    response = requests.get(qrcode_api_url)
    if response.status_code == 200:
        json_data = response.json()
        # 返回 token 和二维码数据给前端
        return jsonify(json_data)
    else:
        return jsonify({"error": "无法获取 token"}), 500

@app.route('/api/status')
def get_status():
    sign = request.args.get('sign')
    time = request.args.get('time')
    uid = request.args.get('uid')

    # 请求二维码扫描状态
    status_api_url = "https://qrcodeapi.115.com/get/status/"
    status_url = f"{status_api_url}?sign={sign}&time={time}&uid={uid}"
    response = requests.get(status_url)
    if response.status_code == 200:
        json_data = response.json()
        return jsonify(json_data)
    else:
        return jsonify({"error": "无法获取扫码状态"}), 500

@app.route('/api/result')
def get_result():
    # 获取扫码后的 cookie 数据
    app_name = request.args.get('app')
    uid = request.args.get('uid')

    # 请求扫码结果
    result_url = f"https://passportapi.115.com/app/1.0/{app_name}/1.0/login/qrcode/"
    payload = {'account': uid}
    response = requests.post(result_url, data=payload)

    if response.status_code == 200:
        json_data = response.json()
        cookie_data = json_data.get('data', {}).get('cookie', {})
        cookie_str = '; '.join([f"{key}={value}" for key, value in cookie_data.items()])

        # 返回 cookie 数据给前端
        return jsonify({"state": json_data["state"], "cookie": cookie_str})
    else:
        return jsonify({"error": "无法获取登录结果"}), 500

@app.route('/cookies', methods=['GET', 'POST'])
@app.route('/cookies.html', methods=['GET', 'POST'])
@login_required
def cookies():
    if request.method == 'POST':
        # 获取 POST 请求中的 cookies 和 app 信息
        cookies_str = request.form.get('cookies')
        app_name = request.form.get('app')

        # 确保 cookies 和 app 存在
        if not cookies_str or not app_name:
            return jsonify({'message': 'Missing cookies or app parameter'}), 400

        with open(cookies_path, 'w') as f:
            f.write(cookies_str)
        # 返回成功的响应
        return jsonify({'message': 'Cookies saved successfully'}), 200

    # 如果是GET请求，渲染 cookies.html 页面
    return render_template('cookies.html')

@app.get("/log")
@app.get("/log.html")
@login_required
def log_view():
    return render_template('log.html')

@app.route('/log_data')
@login_required
def log_data():
    return Response(read_log_file(), content_type='text/plain')

@app.route('/sync_all', methods=['POST'])
def sync_all():
    if request.method == 'POST':
        client = P115Client(cookies_path)
        if not client.login_status():
            return redirect(url_for('cookies'))
        flash('开始全量同步')
        sync_from_beginning(client)
    return redirect(url_for('sync_files'))

@app.route('/sync_new', methods=['POST'])
def sync_new():
    if request.method == 'POST':
        client = P115Client(cookies_path)
        if not client.login_status():
            return redirect(url_for('cookies'))
        flash('开始增量同步')
        sync_from_now(client)
    return redirect(url_for('sync_files'))

@app.route('/sync')
@app.route('/sync.html')
@login_required
def sync_files():
    files = {}
    if os.path.exists(sync_file):
        with open(sync_file, 'r') as fp:
            files = yaml.safe_load(fp) or {}  # 确保文件为空时返回空字典

    for key, value in files.items():
        if 'filetype' not in value:
            flash('检测到旧的格式，请删除sync.yaml然后重建同步目录')
            files = {}
            break

    return render_template('sync.html', items=files)

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
    start_scheduler()  # 启动定时任务调度器
    app.run(host='0.0.0.0', port=app_port)
