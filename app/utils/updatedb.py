#!/usr/bin/env python3
# encoding: utf-8

__author__ = "ChenyangGao <https://chenyanggao.github.io>"
__version__ = (0, 0, 13, 3)
__all__ = ["updatedb", "updatedb_one", "updatedb_tree"]
__doc__ = "遍历 115 网盘的目录信息导出到数据库"
__requirements__ = ["p115client", "posixpatht"]
__licence__ = "GPLv3"

if __name__ == "__main__":
    from argparse import ArgumentParser, RawTextHelpFormatter

    parser = ArgumentParser(
        formatter_class=RawTextHelpFormatter, 
        description=__doc__, 
    )
    parser.add_argument("top_dirs", metavar="dir", nargs="*", help="""\
115 目录，可以传入多个，如果不传默认为 0
允许 3 种类型的目录
    1. 整数，视为目录的 id
    2. 形如 "/名字/名字/..." 的路径，最前面的 "/" 可以省略，本程序会尝试获取对应的 id
    3. 形如 "根目录 > 名字 > 名字 > ..." 的路径，来自点击文件的【显示属性】，在【位置】这部分看到的路径，本程序会尝试获取对应的 id
""")
    parser.add_argument("-c", "--cookies", help="115 登录 cookies，优先级高于 -cp/--cookies-path")
    parser.add_argument("-cp", "--cookies-path", default="", help="cookies 文件保存路径，默认为当前工作目录下的 115-cookies.txt")
    parser.add_argument("-f", "--dbfile", default="", help="sqlite 数据库文件路径，默认为在当前工作目录下的 f'115-{user_id}.db'")
    parser.add_argument("-cl", "--clean", action="store_true", help="任务完成后清理数据库，以节约空间")
    parser.add_argument("-st", "--auto-splitting-threshold", type=int, default=100_000, help="自动拆分的文件数阈值，大于此值时，自动进行拆分，如果 = 0，则总是拆分，如果 < 0，则总是不拆分，默认值 100,000（10 万）")
    parser.add_argument("-sst", "--auto-splitting-statistics-timeout", type=float, default=3, help="自动拆分前的执行文件数统计的超时时间（秒），大于此值时，视为文件数无穷大，如果 <= 0，视为永不超时，默认值 3")
    parser.add_argument("-nm", "--no-dir-moved", action="store_true", help="声明没有目录被移动或改名（但可以有目录被新增或删除），这可以加快批量拉取时的速度")
    parser.add_argument("-nr", "--not-recursive", action="store_true", help="不遍历目录树：只拉取顶层目录，不递归子目录")
    parser.add_argument("-v", "--version", action="store_true", help="输出版本号")

    args = parser.parse_args()
    if args.version:
        print(".".join(map(str, __version__)))
        raise SystemExit(0)

try:
    from httpx import HTTPStatusError, ReadTimeout
    from p115client import check_response, P115Client
    from p115client.const import APP_TO_SSOENT
    from p115client.exception import AuthenticationError, BusyOSError, DataError
    from p115client.tool.edit import update_desc, update_star
    from p115client.tool.iterdir import ensure_attr_path, filter_na_ids, get_path_to_cid, iter_stared_dirs, DirNode
    from posixpatht import escape, joins, normpath
except ImportError:
    from sys import executable
    from subprocess import run
    run([executable, "-m", "pip", "install", "-U", *__requirements__], check=True)
    from httpx import HTTPStatusError, ReadTimeout
    from p115client import check_response, P115Client
    from p115client.const import APP_TO_SSOENT
    from p115client.exception import AuthenticationError, BusyOSError, DataError
    from p115client.tool.edit import update_desc, update_star
    from p115client.tool.iterdir import ensure_attr_path, filter_na_ids, get_path_to_cid, iter_stared_dirs, DirNode
    from posixpatht import escape, joins, normpath

import logging

from collections import deque
from collections.abc import Callable, Collection, Iterator, Iterable, Mapping, Set
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from errno import EBUSY, ENOENT, ENOTDIR
from itertools import takewhile
from math import isnan, isinf
from sqlite3 import connect, Connection, Cursor
from _thread import allocate_lock, LockType
from time import perf_counter
from types import MethodType
from typing import cast, Final


# NOTE: 目录的 id 到它的 名字 和 上级目录 id 的映射
ID_TO_DIRNODE: Final[dict[int, tuple[str, int] | DirNode]] = {}
# NOTE: 初始化日志对象
logger = logging.Logger("115-updatedb", level=logging.INFO)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter(
    "[\x1b[1m%(asctime)s\x1b[0m] (\x1b[1;36m%(levelname)s\x1b[0m) "
    "\x1b[0m\x1b[1;35m%(name)s\x1b[0m \x1b[5;31m➜\x1b[0m %(message)s"
))
logger.addHandler(handler)
_get_cookies = None


def generate_cookies_factory(
    client: P115Client, 
    app: None | str = None, 
) -> Callable[[], str]:
    """利用一个已登录设备的 cookies，产生另一个设备的若干 cookies

    :param client: 115 网盘客户端对象
    :param app: 自动扫码后绑定的 app

    :return: 函数，调用以返回一个 cookies
    """
    if app:
        not_allowed_apps = ("web", "desktop", "harmony", "linux", "mac", "windows")
        if app in not_allowed_apps:
            raise ValueError(f"don't use app in {not_allowed_apps}")
        elif APP_TO_SSOENT.get(app) == client.login_ssoent:
            raise ValueError("may cause login device conflicts")
    else:
        app = client.login_app()
        if app == "alipaymini":
            app = "tv"
        else:
            app = "alipaymini"
    refresh_token = client.login_without_app()
    def call() -> str:
        nonlocal refresh_token
        while True:
            try:
                resp = client.login_qrcode_scan_result(refresh_token, app, timeout=3)
            except ReadTimeout:
                continue
            if not resp["state"]:
                if resp.get("errno") == 40101017:
                    refresh_token = client.login_without_app()
                    continue
                else:
                    check_response(resp)
            return "; ".join(f"{k}={v}" for k, v in resp["data"]["cookie"].items())
    return call


def cookies_pool(
    client: P115Client, 
    app: None | str = None, 
    cooldown_time: int | float = 3, 
    lock: bool | LockType = True, 
) -> Callable[[], tuple[str, Callable[[], None]]]:
    """cookies 池

    :param client: 115 网盘客户端对象
    :param app: 自动扫码后绑定的 app
    :param cooldown_time: cookies 的冷却时间
    :param lock: 多线程锁，如果不需要锁，传入 False

    :return: 返回一个函数，调用后返回一个元组，包含 cookies 和 一个调用以在完成后把 cookies 返还池中
    """
    generate_cookies = generate_cookies_factory(client, app)
    dq: deque[tuple[str, float]] = deque()
    push, pop = dq.append, dq.popleft
    def call():
        if dq and dq[0][1] + cooldown_time < perf_counter():
            cookies = pop()[0]
        else:
            cookies = generate_cookies()
        return cookies, lambda: push((cookies, perf_counter()))
    if lock is False:
        return call
    if lock is True:
        lock = allocate_lock()
    def locked_call():
        with lock:
            return call()
    return locked_call


def get_status(e: BaseException, /) -> None | int:
    status = (
        getattr(e, "status", None) or 
        getattr(e, "code", None) or 
        getattr(e, "status_code", None)
    )
    if status is None and hasattr(e, "response"):
        response = e.response
        status = (
            getattr(response, "status", None) or 
            getattr(response, "code", None) or 
            getattr(response, "status_code", None)
        )
    return status


def call_wrap_with_cookies_pool(method: MethodType, /, *args, headers=None, **kwds):
    global _get_cookies
    client_back = cast(P115Client, method.__self__)
    if _get_cookies is None:
        _get_cookies = cookies_pool(cast(P115Client, client_back))
    client = P115Client("")
    client.session = client_back.session
    method = MethodType(method.__func__, client)
    cookies, revert = _get_cookies()
    while True:
        if headers:
            headers = {**headers, "Cookie": cookies}
        else:
            headers = {"Cookie": cookies}
        try:
            ret = check_response(method(*args, headers=headers, **kwds))
            revert()
            return ret
        except BaseException as e:
            if isinstance(e, AuthenticationError) or get_status(e) == 405:
                cookies, revert = _get_cookies()
                continue
            revert()
            raise


def normalize_path(path: str, /) -> int | str:
    """规范化路径

    :param path: 路径，路径可以是多种形式

        1. 自然数，视为 id
        2. 形如 "/名字/名字/..." 的路径，最前面的 "/" 可以省略
        3. 形如 "根目录 > 名字 > 名字 > ..." 的路径，来自点击文件的【显示属性】，在【位置】这部分看到的路径

    :return: 解析路径，返回相应值

        - 如果可以被解析为整数，则返回此数，作为 id
        - 如果被解析为根目录 "/"，则返回 0
        - 否则，对路径进行一些整理，并解析 "." 和  ".."，然后返回解析后的路径
    """
    if path in ("", "0", ".", "..", "/"):
        return 0
    if not path.startswith("0") and path.isdecimal():
        return int(path)
    if path.startswith("根目录 > "):
        patht = path.split(" > ")
        if len(patht) == 1:
            return 0
        patht[0] = ""
        return joins(patht)
    path = normpath("/" + path)
    if path == "/":
        return 0
    return path


def normalize_attr(info: Mapping, /) -> dict:
    """筛选和规范化数据的名字，以便插入 `data` 表

    :param info: 原始数据

    :return: 经过规范化后的数据
    """
    if "fn" in info:
        is_dir = info["fc"] == "0"
        return {
            "id": int(info["fid"]), 
            "parent_id": int(info["pid"]), 
            "pickcode": info["pc"], 
            "name": info["fn"], 
            "size": int(info.get("fs") or 0), 
            "sha1": info.get("sha1") or "", 
            "is_dir": is_dir, 
            "is_image": not is_dir and info.get("thumb", "").startswith("?s="), 
            "ctime": int(info["uppt"]), 
            "mtime": int(info["upt"])
        }
    else:
        is_dir = "fid" not in info
        if is_dir:
            attr: dict = {"id": int(info["cid"]), "parent_id": int(info["pid"])}
        else:
            attr = {"id": int(info["fid"]), "parent_id": int(info["cid"])}
        attr["pickcode"] = info["pc"]
        attr["name"] = info["n"]
        attr["size"] = int(info.get("s") or 0)
        attr["sha1"] = info.get("sha") or ""
        attr["is_dir"] = is_dir
        attr["is_image"] = not is_dir and info.get("class") in ("PIC", "JG_PIC")
        attr["ctime"] = int(info.get("tp", 0))
        attr["mtime"] = int(info.get("te", 0))
        return attr


def normalize_dir_attr(info: Mapping, /) -> dict:
    """筛选和规范化数据的名字，以便插入 `dir` 表

    :param info: 原始数据

    :return: 经过规范化后的数据
    """
    if "fn" in info:
        return {
            "id": int(info["fid"]), 
            "parent_id": int(info["pid"]), 
            "pickcode": info["pc"], 
            "name": info["fn"], 
            "ctime": int(info["uppt"]), 
            "mtime": int(info["upt"]), 
        }
    else:
        return {
            "id": int(info["cid"]), 
            "parent_id": int(info["pid"]), 
            "pickcode": info["pc"], 
            "name": info["n"], 
            "ctime": int(info["tp"]), 
            "mtime": int(info["te"]), 
        }


def get_dir_path(cid: int = 0, /) -> str:
    """由目录的 id 获取它的 路径

    :param cid: 目录的 id

    :return: 目录的路径
    """
    if not cid:
        return "/"
    parts: list[str] = []
    add = parts.append
    while cid:
        name, cid = ID_TO_DIRNODE[cid]
        add(escape(name))
    add("")
    return "/".join(reversed(parts))


@contextmanager
def transaction(con: Connection | Cursor, /):
    """执行一次数据库提交（commit）

    :param con: 数据库连接或游标
    """
    if isinstance(con, Cursor):
        con = con.connection
    try:
        yield
    except:
        con.rollback()
        raise
    else:
        con.commit()


def execute_commit(
    con: Connection | Cursor, 
    /, 
    sql: str, 
    params = None, 
    executemany: bool = False, 
) -> Cursor:
    """执行一个 sql 语句，并自动提交（commit）和回滚（rollback）

    :param con: 数据库连接或游标
    :param sql: sql 语句
    :param params: 参数，用于填充 sql 中的占位符
    :param executemany: 如果为 True，则执行 `.executemany(sql, params)`，否则执行 `.execute(sql, params)`

    :return: 游标
    """
    if isinstance(con, Connection):
        cur = con.cursor()
    else:
        cur = con
        con = cur.connection
    try:
        if executemany:
            cur = con.executemany(sql, params)
        elif params is None:
            cur = con.execute(sql)
        else:
            cur = con.execute(sql, params)
        con.commit()
        return cur
    except BaseException:
        con.rollback()
        raise


def initdb(con: Connection | Cursor, /) -> Cursor:
    """初始化数据库，会尝试创建一些表、索引、触发器、扩展函数等，并把表的 "journal_mode" 改为 WAL (write-ahead-log)

    :param con: 数据库连接或游标

    :return: 游标
    """
    conn: Connection = con.connection if isinstance(con, Cursor) else con
    conn.create_function("escape_name", 1, escape)
    return con.executescript("""\
-- 修改日志模式为 WAL (write-ahead-log)
PRAGMA journal_mode = WAL;

-- 创建 data 表
CREATE TABLE IF NOT EXISTS data (
    id INTEGER NOT NULL PRIMARY KEY,   -- 文件或目录的 id
    parent_id INTEGER NOT NULL,        -- 上级目录的 id
    pickcode TEXT NOT NULL DEFAULT '', -- 提取码，下载时需要用到
    name TEXT NOT NULL,                -- 名字
    size INTEGER NOT NULL DEFAULT 0,   -- 文件大小
    sha1 TEXT NOT NULL DEFAULT '',     -- 文件的 sha1 散列值
    is_dir INTEGER NOT NULL CHECK(is_dir IN (0, 1)), -- 是否目录
    is_image INTEGER NOT NULL CHECK(is_image IN (0, 1)) DEFAULT 0, -- 是否图片
    ctime INTEGER NOT NULL DEFAULT 0,  -- 创建时间戳，一旦设置就不会更新
    mtime INTEGER NOT NULL DEFAULT 0,  -- 更新时间戳，如果名字、备注被设置（即使值没变），或者（如果自己是目录）进出回收站或增删直接子节点或设置封面，会更新此值，但移动并不更新
    path TEXT NOT NULL DEFAULT '',     -- 路径
    updated_at DATETIME DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+08:00', 'now', '+8 hours')) -- 最近一次更新时间
);

-- 创建 dir 表，用来存储所有看到的目录数据，只增改而不删
CREATE TABLE IF NOT EXISTS dir (
    id INTEGER NOT NULL PRIMARY KEY,   -- 目录的 id
    parent_id INTEGER NOT NULL,        -- 上级目录的 id
    pickcode TEXT NOT NULL DEFAULT '', -- 提取码
    name TEXT NOT NULL,                -- 名字
    ctime INTEGER NOT NULL DEFAULT 0,  -- 创建时间戳，一旦设置就不会更新
    mtime INTEGER NOT NULL DEFAULT 0   -- 更新时间戳，如果名字、备注被设置（即使值没变），或者进出回收站，或者增删直接子节点，或者设置封面，会更新此值，但移动并不更新
);

-- 创建 event 表，用于记录 data 表上发生的 'insert'、'update' 和 'delete' 事件
CREATE TABLE IF NOT EXISTS event (
    _id INTEGER PRIMARY KEY AUTOINCREMENT, -- 主键
    type TEXT, -- 类型，可能是 'insert'、'update' 或 'delete' 之一
    old JSON, -- 旧数据
    new JSON, -- 新数据
    summary JSON NOT NULL DEFAULT '{}', -- 概要，发生的事件集
    created_at DATETIME DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+08:00', 'now', '+8 hours')) -- 创建时间
);

-- 触发器，记录 data 表 'insert'
CREATE TRIGGER IF NOT EXISTS trg_data_insert
AFTER INSERT ON data
FOR EACH ROW
BEGIN
    INSERT INTO event(type, new, summary) VALUES (
        'insert', 
        json_object(
            'id', new.id, 
            'parent_id', new.parent_id, 
            'pickcode', new.pickcode, 
            'name', new.name, 
            'size', new.size, 
            'sha1', new.sha1, 
            'is_dir', new.is_dir, 
            'is_image', new.is_image, 
            'ctime', new.ctime, 
            'mtime', new.mtime, 
            'path', new.path
        ),
        json_object('add', new.path)
    );
END;

-- 触发器，记录 data 表 'update'
CREATE TRIGGER IF NOT EXISTS trg_data_update
AFTER UPDATE ON data 
FOR EACH ROW
BEGIN
    UPDATE data SET updated_at = strftime('%Y-%m-%dT%H:%M:%f+08:00', 'now', '+8 hours') WHERE id = NEW.id;
    INSERT INTO event(type, old, new, summary) 
    SELECT
        'update', 
        json_object(
            'id', old.id, 
            'parent_id', old.parent_id, 
            'pickcode', old.pickcode, 
            'name', old.name, 
            'size', old.size, 
            'sha1', old.sha1, 
            'is_dir', old.is_dir, 
            'is_image', old.is_image, 
            'ctime', old.ctime, 
            'mtime', old.mtime, 
            'path', old.path
        ), 
        json_object(
            'id', new.id, 
            'parent_id', new.parent_id, 
            'pickcode', new.pickcode, 
            'name', new.name, 
            'size', new.size, 
            'sha1', new.sha1, 
            'is_dir', new.is_dir, 
            'is_image', new.is_image, 
            'ctime', new.ctime, 
            'mtime', new.mtime, 
            'path', new.path
        ), 
        json_object(
            'move', CASE WHEN old.parent_id != new.parent_id OR (old.name = new.name AND old.path != new.path) THEN json_array(old.path, new.path) END, 
            'rename', CASE WHEN old.name != new.name THEN json_array(old.name, new.name) END
        )
    WHERE old.mtime != new.mtime OR old.name != new.name OR old.path != new.path;
END;

-- 触发器，记录 data 表 'delete'
CREATE TRIGGER IF NOT EXISTS trg_data_delete
AFTER DELETE ON data
FOR EACH ROW
BEGIN
    INSERT INTO event(type, old, summary) VALUES (
        'delete', 
        json_object(
            'id', old.id, 
            'parent_id', old.parent_id, 
            'pickcode', old.pickcode, 
            'name', old.name, 
            'size', old.size, 
            'sha1', old.sha1, 
            'is_dir', old.is_dir, 
            'is_image', old.is_image, 
            'ctime', old.ctime, 
            'mtime', old.mtime, 
            'path', old.path
        ), 
        json_object('delete', old.path)
    );
END;

-- 创建索引
CREATE INDEX IF NOT EXISTS idx_data_parent_id ON data(parent_id);
CREATE INDEX IF NOT EXISTS idx_data_path ON data(path);
CREATE INDEX IF NOT EXISTS idx_dir_mtime ON dir(mtime);
CREATE INDEX IF NOT EXISTS idx_event_created_at ON event(created_at);
""")


def select_parent_ids(
    con: Connection | Cursor, 
    ids: Iterable[int], 
    /, 
) -> list[int]:
    """获取一组 id 对应的 parent_id，经过去重

    :param con: 数据库连接或游标
    :param ids: 一组 id

    :return: 一组 id, 已经去重
    """
    sql = "SELECT DISTINCT parent_id FROM data WHERE id IN (%s)" % (",".join(map(str, ids)) or "NULL")
    return [row[0] for row in con.execute(sql)]


def select_subtree_ids(
    con: Connection | Cursor, 
    /, 
    root: int | str = 0, 
) -> list[int]:
    """获取以 `root` 为根的目录树的所有节点的 id

    :param con: 数据库连接或游标
    :param root: 根节点的 id 或 路径

    :return: 一组 id
    """
    if isinstance(root, int):
        sql = """\
WITH RECURSIVE t(id) AS (
    SELECT :root
    UNION ALL
    SELECT data.id FROM t JOIN data WHERE (data.parent_id = t.id)
)
SELECT * FROM t
"""
    else:
        sql = """\
SELECT id 
FROM data 
WHERE path = :root OR path LIKE :root || '/%'
"""
    return [row[0] for row in con.execute(sql, {"root": root})]


def select_subdir_ids(
    con: Connection | Cursor, 
    parent_id: int = 0, 
    /, 
) -> list[int]:
    """获取某个目录之下的所有子目录的 id

    :param con: 数据库连接或游标
    :param parent_id: 父目录的 id

    :return: 一组 id
    """
    sql = "SELECT id FROM data WHERE parent_id=? AND is_dir=1"
    return [row[0] for row in con.execute(sql, (parent_id,))]


def select_mtime_groups(
    con: Connection | Cursor, 
    parent_id: int = 0, 
    /, 
    tree: bool = False, 
) -> dict[int, set[int]]:
    """获取某个目录之下的节点（不含此节点本身），按 mtime 进行分组，相同 mtime 的 id 归入同一组

    :param con: 数据库连接或游标
    :param parent_id: 父目录的 id
    :param tree: 是否拉取目录树，如果为 True，则拉取全部后代的文件节点（不含目录节点），如果为 False，则只拉取子节点（含目录节点）

    :return: 字典，表示相同 mtime 的 id 的集合，所以 key 是 mtime，value 是一组 id 的集合
    """
    if tree:
        sql = """\
WITH RECURSIVE t AS (
    SELECT id, mtime, is_dir
    FROM data
    WHERE parent_id=?
    UNION ALL
    SELECT data.id, data.mtime, data.is_dir
    FROM t JOIN data ON (data.parent_id = t.id)
)
SELECT mtime, id
FROM t
WHERE is_dir = 0
ORDER BY mtime DESC
"""
    else:
        sql = """\
SELECT mtime, id
FROM data
WHERE parent_id=? AND mtime != 0
ORDER BY mtime DESC
"""
    s: set[int]
    d: dict[int, set[int]] = {}
    add = set.add
    last_mtime = 0
    for mtime, id in con.execute(sql, (parent_id,)):
        if last_mtime == mtime:
            add(s, id)
        else:
            s = d[mtime] = {id}
            last_mtime = mtime
    return d


def select_dangling_ids(
    con: Connection | Cursor, 
    /, 
) -> set[int]:
    """找出所有的悬空节点的 id

    .. note::
        悬空节点，就是此节点有一个祖先节点的 id，不为 0 且不在 `data` 表中

    :param con: 数据库连接或游标

    :return: 一组悬空节点的 id 的集合
    """
    d = dict(con.execute("SELECT id, parent_id FROM data"))
    temp: list[int] = []
    ok_ids: set[int] = set()
    na_ids: set[int] = set()
    push = temp.append
    clear = temp.clear
    update_ok = ok_ids.update
    update_na = na_ids.update
    for k, v in d.items():
        try:
            push(k)
            while k := d[k]:
                if k in ok_ids:
                    update_ok(temp)
                    break
                elif k in na_ids:
                    update_na(temp)
                    break
                push(k)
            else:
                update_ok(temp)
        except KeyError:
            update_na(temp)
        finally:
            clear()
    return na_ids


def select_items_from_dir(
    con: Connection | Cursor, 
    ids: Iterable[int], 
    /, 
) -> list[dict]:
    """使用一组目录的 id 从 `dir` 表查询对应的数据

    :param con: 数据库连接或游标
    :param ids: 一组 id

    :return: 一组数据，对应 `ids`
    """
    fields = ("id", "parent_id", "pickcode", "name", "ctime", "mtime", "size", "sha1", "is_dir", "is_image")
    sql = """\
SELECT id, parent_id, pickcode, name, ctime, mtime, 0 AS size, '' AS sha1, 1 AS is_dir, 0 AS is_image 
FROM dir WHERE id in (%s)""" % (",".join(map(str, ids)) or "NULL")
    return [dict(zip(fields, row)) for row in con.execute(sql)]


def insert_items(
    con: Connection | Cursor, 
    items: Mapping | Iterable[Mapping], 
    /, 
    commit: bool = True, 
) -> Cursor:
    """向 `data` 表插入一组数据

    :param con: 数据库连接或游标
    :param items: 一组数据
    :param commit: 是否提交

    :return: 游标
    """
    sql = """\
INSERT INTO
    data(id, parent_id, pickcode, name, size, sha1, is_dir, is_image, ctime, mtime, path)
VALUES
    (:id, :parent_id, :pickcode, :name, :size, :sha1, :is_dir, :is_image, :ctime, :mtime, :path)
ON CONFLICT(id) DO UPDATE SET
    parent_id = excluded.parent_id,
    pickcode  = excluded.pickcode,
    name      = CASE WHEN is_dir THEN name ELSE excluded.name END,
    ctime     = excluded.ctime,
    mtime     = excluded.mtime,
    path      = CASE WHEN is_dir THEN path ELSE excluded.path END
"""
    if isinstance(items, Mapping):
        items = items,
    if commit:
        return execute_commit(con, sql, items, executemany=True)
    else:
        return con.executemany(sql, items)


def insert_ancestor_items(
    con: Connection | Cursor, 
    items: list[dict], 
    /, 
    commit: bool = True, 
) -> Cursor:
    """向 `data` 表插入一组数据

    :param con: 数据库连接或游标
    :param items: 一组数据
    :param commit: 是否提交

    :return: 游标
    """
    sql = """\
INSERT INTO
    data(id, parent_id, name, path, is_dir)
VALUES
    (:id, :parent_id, :name, :path, 1)
ON CONFLICT(id) DO UPDATE SET
    parent_id = excluded.parent_id
"""
    path = ""
    for item in items:
        path = item["path"] = path + "/" + escape(item["name"])
    if commit:
        return execute_commit(con, sql, items, executemany=True)
    else:
        return con.executemany(sql, items)


def insert_dir_items(
    con: Connection | Cursor, 
    items: Mapping | Iterable[Mapping], 
    /, 
    commit: bool = True, 
) -> Cursor:
    """向 `dir` 表插入一组数据

    :param con: 数据库连接或游标
    :param items: 一组数据
    :param commit: 是否提交

    :return: 游标
    """
    sql = """\
INSERT INTO
    dir(id, parent_id, pickcode, name, ctime, mtime)
VALUES
    (:id, :parent_id, :pickcode, :name, :ctime, :mtime)
ON CONFLICT(id) DO UPDATE SET
    parent_id = excluded.parent_id,
    pickcode  = excluded.pickcode,
    name      = excluded.name,
    ctime     = excluded.ctime,
    mtime     = excluded.mtime
"""
    if isinstance(items, Mapping):
        items = items,
    if commit:
        return execute_commit(con, sql, items, executemany=True)
    else:
        return con.executemany(sql, items)


def insert_dir_incomplete_items(
    con: Connection | Cursor, 
    items: Mapping | Iterable[Mapping], 
    /, 
    commit: bool = True, 
) -> Cursor:
    """向 `dir` 表插入一组数据，只使用数据中 "id"、"name"、"parent_id" 字段

    :param con: 数据库连接或游标
    :param items: 一组数据
    :param commit: 是否提交

    :return: 游标
    """
    sql = """\
INSERT INTO
    dir(id, parent_id, name)
VALUES
    (:id, :parent_id, :name)
ON CONFLICT(id) DO UPDATE SET
    parent_id = excluded.parent_id,
    name      = excluded.name
"""
    if isinstance(items, Mapping):
        items = items,
    if commit:
        return execute_commit(con, sql, items, executemany=True)
    else:
        return con.executemany(sql, items)


def delete_items(
    con: Connection | Cursor, 
    ids: int | Iterable[int], 
    /, 
    commit: bool = True, 
) -> tuple[Cursor, int]:
    """使用 id 去筛选和删除一组数据

    :param con: 数据库连接或游标
    :param ids: 一组 id，会被删除
    :param commit: 是否提交

    :return: 游标 和 删除的数据量
    """
    if isinstance(ids, int):
        cond = f"id = {ids:d}"
    else:
        cond = "id IN (%s)" % (",".join(map(str, ids)) or "NULL")
    sql = "DELETE FROM data WHERE " + cond
    if commit:
        cur = execute_commit(con, sql)
    else:
        cur = con.execute(sql)
    return cur, cur.rowcount


def delete_dangling_items(
    con: Connection | Cursor, 
    /, 
    commit: bool = True, 
) -> tuple[Cursor, int]:
    """删除所有的悬空节点

    .. note::
        所谓悬空，意指通过 paren_id 字段往上找寻，存在某个 paren_id != 0 且不在数据库中

    :param con: 数据库连接或游标
    :param commit: 是否提交

    :return: 游标 和 删除的数据量
    """
    return delete_items(con, select_dangling_ids(con), commit=commit)


def delete_na_dirs(
    con, 
    /, 
    client: P115Client, 
    commit: bool = True, 
) -> tuple[Cursor, int]:
    """删除无效的目录，也就是数据库中存在，但是网盘中不存在的目录

    :param con: 数据库连接或游标
    :param client: 115 网盘客户端对象
    :param commit: 是否提交

    :return: 游标 和 删除的数据量
    """
    sql = """\
SELECT data.id
FROM data LEFT JOIN data AS data2 ON (data.id = data2.parent_id)
WHERE data.is_dir AND data2.id IS NULL
"""
    na_ids = filter_na_ids(client, (row[0] for row in con.execute(sql)))
    return delete_items(con, na_ids, commit=commit)


def update_path(
    con: Connection | Cursor, 
    /, 
    root_id: int = 0, 
    ids: None | Iterable[int] = None, 
    commit: bool = True, 
) -> tuple[Cursor, int, int]:
    """以 `dir` 表为准，和 `data` 表比对，找出所有 "name" 或 "parent_id" 不同的目录，然后批量更新 `data` 表中的数据

    :param con: 数据库连接或游标
    :param root_id: 根目录的 id，如果此 id > 0，则凡是之前在此目录中，将在更新后不在的，都要被删除
    :param commit: 是否提交

    :return: 3 元组，游标、更新的数据量、删除的数据量
    """
    if isinstance(con, Connection):
        cur = con.cursor()
    else:
        cur = con
        con = cur.connection
    if root_id > 0:
        sql = "SELECT path FROM data WHERE id=?"
        row = cur.execute(sql, (root_id,)).fetchone()
        if row is None:
            root_id = 0
        else:
            root = row[0] + "/"
            root_new = get_dir_path(root_id) + "/"
    else:
        root_id = 0
    if ids is None:
        sql = """\
SELECT id, data.path, dir.mtime
FROM data JOIN dir USING (id)
WHERE data.name != dir.name OR data.parent_id != dir.parent_id
ORDER BY path DESC
"""
    else:
        sql = """\
SELECT data.id, data.path, dir.mtime
FROM data JOIN dir ON (data.id IN (%s) AND data.id = dir.id)
WHERE data.name != dir.name OR data.parent_id != dir.parent_id
ORDER BY path DESC
""" % (",".join(map(str, ids)) or "NULL")
    updated = 0
    deleted = 0
    for cid, path, mtime in cur.execute(sql):
        name, pid = ID_TO_DIRNODE[cid]
        path_new = get_dir_path(cid)
        if root_id and path.startswith(root) and not path_new.startswith(root_new):
            cur.execute("DELETE FROM data WHERE id=? OR path LIKE ? || '/%'", (cid, path))
            deleted += cur.rowcount
        else:
            cur.execute("UPDATE data SET name=?, parent_id=?, path=?, mtime=? WHERE id=?", (name, pid, path_new, mtime, cid))
            cur.execute("UPDATE data SET path = ? || SUBSTR(path, ?) WHERE path LIKE ? || '/%'", (path_new, len(path) + 1, path))
            updated += cur.rowcount + 1
    if commit:
        con.commit()
    return cur, updated, deleted


def load_id_to_dirnode(con: Connection | Cursor, /):
    """把 `dir` 表的数据加载到全局变量 `ID_TO_DIRNODE` 中

    :param con: 数据库连接或游标
    """
    sql = "SELECT id, name, parent_id FROM dir"
    for id, name, parent_id in con.execute(sql):
        ID_TO_DIRNODE[id] = (name, parent_id)


def update_id_to_dirnode(
    con: Connection | Cursor, 
    /, 
    client: P115Client, 
) -> list[dict]:
    """从网上增量拉取目录数据，并更新到 `dir` 表和全局变量 `ID_TO_DIRNODE` 中

    :param con: 数据库连接或游标
    :param client: 115 网盘客户端对象
    """
    sql = "SELECT COALESCE(MAX(mtime), 0) FROM dir"
    mtime, = con.execute(sql).fetchone()
    data: list[dict] = list(takewhile(lambda attr: attr["mtime"] > mtime, iter_stared_dirs(
        client, 
        order="user_utime", 
        asc=0, 
        first_page_size=32, 
        id_to_dirnode=ID_TO_DIRNODE, 
        normalize_attr=normalize_dir_attr, 
        app="android", 
    )))
    if data:
        insert_dir_items(con, data)
    return data


# TODO: 如果发生 id 重复，但 count 没变，则并不报错，会丢弃重复的 id 的数据，然后跳过而不返回，并增加计数器，等拉取完后，从头部开始再取一次（每取出一个未见到过的元素，计数器减 1，直到计数器为 0）
# TODO: 每次都要记录上一次的头部元素是哪个，因为可能反复要从头部开始，去追更，直到把所有更新都找全（如果找到上次的头部时，未遇到重复id，而计数器不为 0，则报错）
def iterdir(
    client: P115Client, 
    id: int = 0, 
    /, 
    first_page_size: None | int = None, 
    page_size: int = 10_000, 
    payload: dict = {}, 
) -> tuple[int, list[dict], dict[int, dict], Iterator[dict]]:
    """拉取一个目录中的文件或目录的数据

    :param client: 115 网盘客户端对象
    :param id: 目录的 id
    :param first_page_size: 首次拉取的分页大小，如果为 None 或者 <= 0，自动确定
    :param page_size: 分页大小
    :param payload: 其它查询参数

    :return: 4 元组，分别是

        1. 总数
        2. 祖先节点的简略信息（不含根目录）
        3. 已经拉取的文件或目录的数据，key 是文件或目录的 id，value 是相应的数据
        4. 迭代器，用来获取数据
    """
    if page_size <= 0:
        page_size = 10_000
    if not first_page_size or first_page_size <= 0:
        first_page_size = page_size
    payload = {
        "asc": 0, "cid": id, "custom_order": 1, "fc_mix": 1, "o": "user_utime", "offset": 0, 
        "limit": first_page_size, "show_dir": 1, **payload, 
    }
    def fs_files(*a, **k):
        while True:
            try:
                return check_response(client.fs_files_app(*a, **k))
            except DataError:
                if payload["limit"] <= 1150:
                    raise
                payload["limit"] -= 1_000
                if payload["limit"] < 1150:
                    payload["limit"] = 1150
    count = -1
    ancestors: list[dict] = []
    seen: dict[int, dict] = {}
    def get_files():
        nonlocal count
        #resp = call_wrap_with_cookies_pool(client.fs_files, payload)
        resp = fs_files(payload)
        if int(resp["path"][-1]["cid"]) != id:
            if count < 0:
                raise NotADirectoryError(ENOTDIR, f"not a dir or deleted: cid={id}")
            else:
                raise FileNotFoundError(ENOENT, f"no such dir: cid={id}")
        ancestors[:] = (
            {"id": int(info["cid"]), "parent_id": int(info["pid"]), "name": info["name"]} 
            for info in resp["path"][1:]
        )
        if count < 0:
            count = resp["count"]
        elif count != resp["count"]:
            raise BusyOSError(EBUSY, f"detected count changes during iteration: cid={id}")
        return resp
    resp = get_files()
    payload["limit"] = page_size
    def iterate():
        nonlocal resp
        offset = 0
        while True:
            for attr in map(normalize_attr, resp["data"]):
                if attr["id"] in seen:
                    raise BusyOSError(
                        EBUSY, 
                        f"duplicate id found, means that some unpulled items have been updated: cid={id}", 
                    )
                seen[attr["id"]] = attr
                yield attr
            offset += len(resp["data"])
            if offset >= count:
                break
            payload["offset"] = offset
            resp = get_files()
    return count, ancestors, seen, iterate()


def diff_dir(
    con: Connection | Cursor, 
    client: P115Client, 
    id: int = 0, 
    /, 
    tree: bool = False, 
    dir_ids: None | set[int] = None, 
) -> tuple[list[int], list[dict]]:
    """拉取数据，确定哪些记录需要删除或更替

    :param con: 数据库连接或游标
    :param client: 115 网盘客户端对象
    :param id: 目录的 id
    :param tree: 如果为 True，则比对目录树，但仅对文件，即叶子节点，如果为 False，则比对所有直接子节点，包括文件和目录
    :param dir_ids: 用来收集那些更新过的目录 id

    :return: 2 元组，1) 待删除的 id 列表，2) 待更替的数据列表
    """
    stored: dict[int, set[int]] = select_mtime_groups(con, id, tree=tree)
    n = sum(map(len, stored.values()))
    upsert_list: list[dict] = []
    delete_list: list[int] = []
    dirs: list[dict] = []
    upsert_add = upsert_list.append
    dirs_add = dirs.append
    if tree:
        count, ancestors, seen, data_it = iterdir(client, id, first_page_size=128 if n else 0, payload={"show_dir": 0})
    else:
        count, ancestors, seen, data_it = iterdir(client, id, first_page_size=16 if n else 0)
    result = delete_list, upsert_list
    try:
        if not n:
            upsert_list += data_it
            if not tree:
                dirs += (a for a in upsert_list if a["is_dir"])
            return result
        it = iter(stored.items())
        his_mtime, his_ids = next(it)
        for attr in data_it:
            if attr["is_dir"]:
                dirs_add(attr)
            cur_id = attr["id"]
            cur_mtime = attr["mtime"]
            while his_mtime > cur_mtime:
                delete_list += his_ids - seen.keys()
                n -= len(his_ids)
                if not n:
                    upsert_add(attr)
                    upsert_list += data_it
                    return result
                his_mtime, his_ids = next(it)
            if his_mtime == cur_mtime:
                if cur_id in his_ids:
                    n -= 1
                    if count - len(seen) == n:
                        return result
                    his_ids.remove(cur_id)
                else:
                    upsert_add(attr)
            else:
                upsert_add(attr)
        for _, his_ids in it:
            delete_list += his_ids - seen.keys()
        return result
    finally:
        if ancestors:
            insert_dir_incomplete_items(con, ancestors)
            insert_ancestor_items(con, ancestors, commit=False)
            for a in ancestors:
                ID_TO_DIRNODE[a["id"]] = (a["name"], a["parent_id"])
            if dir_ids is not None:
                dir_ids.update(a["id"] for a in ancestors)
        if dirs:
            insert_dir_items(con, dirs)
            for a in dirs:
                ID_TO_DIRNODE[a["id"]] = (a["name"], a["parent_id"])
            if dir_ids is not None:
                dir_ids.update(a["id"] for a in dirs)


def updatedb_one(
    client: str | P115Client, 
    dbfile: None | str | Connection | Cursor = None, 
    id: int = 0, 
    /, 
):
    """
    """
    if isinstance(client, str):
        client = P115Client(client, check_for_relogin=True)
    if not dbfile:
        dbfile = f"115-{client.user_id}.db"
    if isinstance(dbfile, (Connection, Cursor)):
        con = dbfile
        start = perf_counter()
        try:
            dir_ids: set[int] = set()
            to_delete, to_replace = diff_dir(con, client, id, dir_ids=dir_ids)
            with transaction(con):
                if to_delete:
                    delete_items(con, to_delete, commit=False)
                if to_replace:
                    dirname = get_path_to_cid(client, id, id_to_dirnode=ID_TO_DIRNODE)
                    if dirname != "/":
                        dirname += "/"
                    for attr in to_replace:
                        attr["path"] = dirname + escape(attr["name"])
                    insert_items(con, to_replace, commit=False)
                if dir_ids:
                    _, updated, deleted = update_path(con, ids=dir_ids, commit=False)
                else:
                    updated = deleted = 0
        except BaseException as e:
            logger.exception("[\x1b[1;31mFAIL\x1b[0m] %s", id)
            if isinstance(e, (FileNotFoundError, NotADirectoryError)):
                delete_items(con, id)
            raise
        else:
            logger.info(
                "[\x1b[1;32mGOOD\x1b[0m] \x1b[1m%d\x1b[0m, upsert: %d, delete: %d, update_path: %d, cost: %.6f s", 
                id, 
                len(to_replace), 
                len(to_delete) + deleted, 
                updated, 
                perf_counter() - start, 
            )
    else:
        with connect(dbfile, uri=dbfile.startswith("file:")) as con:
            initdb(con)
            load_id_to_dirnode(con)
            updatedb_one(client, con, id)


# TODO: 文件如果被移动位置，并且还在一个根目录之下，由此它自己的 mtime 不变，这要怎么处理？或许需要结合 115 事件
# TODO: 如果从未被拉过，应该得到专门的处理，使用并发，以加快速度，增量拉则不上这种手段
def updatedb_tree(
    client: str | P115Client, 
    dbfile: None | str | Connection | Cursor = None, 
    id: int = 0, 
    /, 
    no_dir_moved: bool = True, 
):
    """
    """
    if isinstance(client, str):
        client = P115Client(client, check_for_relogin=True)
    if not dbfile:
        dbfile = f"115-{client.user_id}.db"
    if isinstance(dbfile, (Connection, Cursor)):
        con = dbfile
        start = perf_counter()
        try:
            to_delete, to_replace = diff_dir(con, client, id, tree=True)
            custom_no_dir_moved = no_dir_moved
            dir_ids: dict[int, dict] = {}
            if to_delete:
                # 找出所有待删除记录的祖先节点 id，并更新它们的 mtime
                all_pids: set[int] = set()
                pids: Collection[int] = to_delete
                while pids := [id for id in select_parent_ids(con, pids) if id != 0 and id not in all_pids]:
                    all_pids.update(pids)
                if all_pids:
                    # 删除可能意味着被移动，而移动并不会更新 mtime，所以需要强制进行更新
                    if not custom_no_dir_moved:
                        update_desc(client, all_pids)
                        no_dir_moved = False
                    # 把所有无效的 id 添加到待删除列表
                    to_delete += filter_na_ids(client, all_pids)
            if to_replace:
                # 找出所有待更新记录的祖先节点 id，并更新它们的 mtime
                all_pids = set()
                na_pids: set[int] = set()
                pids = {ppid for attr in to_replace if (ppid := attr["parent_id"])}
                while pids:
                    all_pids |= pids
                    if find_ids := pids - ID_TO_DIRNODE.keys():
                        update_star(client, find_ids)
                        if custom_no_dir_moved:
                            update_desc(client, find_ids)
                        else:
                            update_desc(client, pids)
                        dir_ids.update((a["id"], a) for a in update_id_to_dirnode(con, client))
                        no_dir_moved = True
                    elif not custom_no_dir_moved:
                        update_desc(client, pids)
                        no_dir_moved = False
                    na_pids.update(pid for pid in pids if pid not in ID_TO_DIRNODE)
                    pids = {ppid for pid in pids - na_pids if (ppid := ID_TO_DIRNODE[pid][1]) and ppid not in all_pids}
                if na_pids:
                   # 直接忽略找不到的目录 id
                   all_pids -= na_pids
                   logging.warning("found some dangling directory ids, please clean them up, otherwise it will slow down the update speed: %r", na_pids)
            # TODO: 想办法减少调用 update_id_to_dirnode，就可以极大减少更新时间，如果前一次拉取后，可以确定后续都可以只在必要时更新，则可以减少大量时间
            if not no_dir_moved:
                dir_ids.update((a["id"], a) for a in update_id_to_dirnode(con, client))
                ensure_attr_path(client, dir_ids.values(), id_to_dirnode=ID_TO_DIRNODE, with_path=False, errors="warn")
            if to_replace: 
                if all_pids:
                    # 把所有相关的目录 id 添加到待更替列表
                    to_replace += select_items_from_dir(con, all_pids)
                ensure_attr_path(client, to_replace, id_to_dirnode=ID_TO_DIRNODE, errors="warn")
            with transaction(con):
                if to_delete:
                    delete_items(con, to_delete, commit=False)
                if to_replace:
                    insert_items(con, to_replace, commit=False)
                if dir_ids:
                    _, updated, deleted = update_path(con, root_id=id, ids=dir_ids, commit=False)
                else:
                    updated = deleted = 0
        except BaseException as e:
            logger.exception("[\x1b[1;31mFAIL\x1b[0m] %s", id)
            if isinstance(e, (FileNotFoundError, NotADirectoryError)):
                delete_items(con, id)
            raise
        else:
            logger.info(
                "[\x1b[1;32mGOOD\x1b[0m] \x1b[1m%d\x1b[0m, upsert: %d, delete: %d, update_path: %d, cost: %.6f s", 
                id, 
                len(to_replace), 
                len(to_delete) + deleted, 
                updated, 
                perf_counter() - start, 
            )
    else:
        with connect(dbfile, uri=dbfile.startswith("file:")) as con:
            initdb(con)
            load_id_to_dirnode(con)
            updatedb_tree(client, con, id, no_dir_moved=no_dir_moved)


def updatedb(
    client: str | P115Client, 
    dbfile: None | str | Connection | Cursor = None, 
    top_dirs: int | str | Iterable[int | str] = 0, 
    auto_splitting_threshold: int = 100_000, 
    auto_splitting_statistics_timeout: None | float = 3, 
    no_dir_moved: bool = True, 
    recursive: bool = True, 
    clean: bool = False, 
):
    """
    """
    if isinstance(client, str):
        client = P115Client(client, check_for_relogin=True)
    if not dbfile:
        dbfile = f"115-{client.user_id}.db"
    if (auto_splitting_statistics_timeout is None or 
        isnan(auto_splitting_statistics_timeout) or 
        isinf(auto_splitting_statistics_timeout) or 
        auto_splitting_statistics_timeout <= 0
    ):
        auto_splitting_statistics_timeout = None
    if isinstance(dbfile, (Connection, Cursor)):
        con = dbfile
        seen: set[int] = set()
        seen_add = seen.add
        dq: deque[int] = deque()
        push, pop = dq.append, dq.popleft
        if isinstance(top_dirs, int):
            top_ids: Collection[int] = (top_dirs,)
        elif isinstance(top_dirs, str):
            top_dir = normalize_path(top_dirs)
            if isinstance(top_dir, int):
                top_ids = (top_dir,)
            else:
                try:
                    resp = check_response(client.fs_dir_getid(top_dir))
                    if not resp["id"]:
                        return
                    top_ids = (int(resp["id"]),)
                except:
                    logger.exception("[\x1b[1;31mFAIL\x1b[0m] %r", top_dirs)
                    return
        else:
            top_ids = set()
            add_id = top_ids.add
            for top_dir in top_dirs:
                if isinstance(top_dir, int):
                    add_id(top_dir)
                else:
                    top_dir = normalize_path(top_dir)
                    if isinstance(top_dir, int):
                        add_id(top_dir)
                    else:
                        try:
                            resp = check_response(client.fs_dir_getid(top_dir))
                            if not resp["id"]:
                                continue
                            add_id(int(resp["id"]))
                        except:
                            logger.exception("[\x1b[1;31mFAIL\x1b[0m] %r", top_dir)
                            continue
            if not top_ids:
                return
        if auto_splitting_threshold > 0:
            executor = ThreadPoolExecutor(max_workers=1)
            submit = executor.submit
            cache_futures: dict[int, Future] = {}
            def get_dir_size(cid: int = 0, /) -> int | float:
                if cid == 0:
                    resp = check_response(client.fs_space_summury())
                    if not resp["type_summury"]:
                        return float("inf")
                    return sum(v["count"] for k, v in resp["type_summury"].items() if k.isupper())
                else:
                    try:
                        resp = client.fs_category_get_app(cid, timeout=auto_splitting_statistics_timeout)
                        if not resp:
                            return 0
                        check_response(resp)
                        return int(resp["count"])
                    except ReadTimeout:
                        logger.info("[\x1b[1;37;43mSTAT\x1b[0m] \x1b[1m%d\x1b[0m, too big, since statistics timeout, consider the size as \x1b[1;3minf\x1b[0m", id)
                        return float("inf")
        try:
            dq.extend(top_ids)
            need_calc_size = recursive and auto_splitting_threshold > 0
            if need_calc_size:
                for cid in top_ids:
                    if cid not in cache_futures:
                        cache_futures[cid] = submit(get_dir_size, cid)
            while dq:
                id = pop()
                if id in seen:
                    logger.warning("[\x1b[1;33mSKIP\x1b[0m]", id)
                    continue
                if auto_splitting_threshold == 0:
                    need_to_split_tasks = True
                elif auto_splitting_threshold < 0:
                    need_to_split_tasks = False
                elif recursive:
                    count = cache_futures[id].result()
                    if count <= 0:
                        seen_add(id)
                        continue
                    need_to_split_tasks = count > auto_splitting_threshold
                    if need_to_split_tasks:
                        logger.info(f"[\x1b[1;37;41mTELL\x1b[0m] \x1b[1m{id}\x1b[0m, \x1b[1;31mbig\x1b[0m ({count:,.0f} > {auto_splitting_threshold:,d}), will be pulled in \x1b[1;4;5;31mmulti batches\x1b[0m")
                    else:
                        logger.info(f"[\x1b[1;37;42mTELL\x1b[0m] \x1b[1m{id}\x1b[0m, \x1b[1;32mfit\x1b[0m ({count:,.0f} <= {auto_splitting_threshold:,d}), will be pulled in \x1b[1;4;5;32mone batch\x1b[0m")
                try:
                    if need_to_split_tasks or not recursive:
                        updatedb_one(client, con, id)
                    else:
                        # TODO: 为了优化，完全可以把星标目录全量拉取一遍，那么 no_dir_moved 就可以安全设置为 False
                        updatedb_tree(client, con, id, no_dir_moved=no_dir_moved)
                except (FileNotFoundError, NotADirectoryError):
                    pass
                except BusyOSError:
                    logger.warning("[\x1b[1;34mREDO\x1b[0m] %s", id)
                    push(id)
                else:
                    seen_add(id)
                    if recursive and need_to_split_tasks:
                        ids = select_subdir_ids(con, id)
                        if ids:
                            dq.extend(ids)
                            if need_calc_size:
                                for cid in ids:
                                    if cid not in cache_futures:
                                        cache_futures[cid] = submit(get_dir_size, cid)
            if clean and top_ids:
                delete_na_dirs(con, client)
                delete_dangling_items(con)
        finally:
            if need_calc_size:
                executor.shutdown(wait=False, cancel_futures=True)
    else:
        with connect(dbfile, uri=dbfile.startswith("file:")) as con:
            initdb(con)
            load_id_to_dirnode(con)
            updatedb(
                client, 
                con, 
                top_dirs=top_dirs, 
                auto_splitting_threshold=auto_splitting_threshold, 
                auto_splitting_statistics_timeout=auto_splitting_statistics_timeout, 
                no_dir_moved=no_dir_moved, 
                recursive=recursive, 
                clean=clean, 
            )
            if clean:
                con.execute("PRAGMA wal_checkpoint;")
                con.execute("VACUUM;")


if __name__ == "__main__":
    if not (cookies := args.cookies):
        from pathlib import Path
        if cookies_path := args.cookies_path:
            cookies = Path(cookies_path)
        else:
            cookies = Path("115-cookies.txt")
    client = P115Client(cookies, check_for_relogin=True, ensure_cookies=True, app="alipaymini")
    updatedb(
        client, 
        dbfile=args.dbfile, 
        auto_splitting_threshold=args.auto_splitting_threshold, 
        auto_splitting_statistics_timeout=args.auto_splitting_statistics_timeout, 
        no_dir_moved=args.no_dir_moved, 
        recursive=not args.not_recursive, 
        top_dirs=args.top_dirs or 0, 
        clean=args.clean, 
    )

# NOTE: 以下这些是待实现的设想 👇
# TODO: 作为模块提供，允许全量更新(updatedb)和增量更新(updatedb_one)，但只允许同时最多一个写入任务
# TODO: 可以起一个服务，其它的程序，可以发送读写任务过来，数据库可以以 fuse 或 webdav 展示
# TODO: 支持多个不同登录设备并发
# TODO: 支持同一个 cookies 并发因子，默认值 1
# TODO: 使用协程进行并发，而非多线程
# TODO: 如果请求超时，则需要进行重试
# TODO: sqlite 的数据库事务和写入会自动加锁，如果有多个程序在并发，则可以等待锁，需要一个超时时间和重试次数
# TODO: iterdir 函数支持并发

# TODO: 如果相同 parent_id 下，有同名的目录，则说明有冲突，需要删掉旧有的（但又由于可能是发生了移动，如果直接删除，可能会导致下次会重新拉取大量数据）
#       parent_id, name, is_dir=1
# TODO: 增加子命令，可以删除目录树（通过根 id 或者根路径）
# TODO: 如果一个文件夹被移动，那么它的更新时间不会变，只是它的上级 id 的更新时间会变，因此必要时，还是需要结合 115 更新事件

# TODO: 增加一个选项，允许对数据进行全量而不是增量更新，这样可以避免一些问题
# TODO: 增加一个选项，如果查询的某个 id 不存在，就把这个 id 的在数据库的数据给删除
# TODO: 为数据库插入弄单独一个线程，就不需要等待数据库插入完成，就可以开始下一批数据拉取
# TODO: 遇到悬空元素，如何处理，是 1) 忽略、2) 删除 3) 移走 还是 4) 报错
# TODO: 还要处理一种情况，和悬空元素有关，某个目录被删除了，后来建立了同名的目录，然后有些文件还是移动入那个被删除的目录，就会造成这些元素悬空，更重要的是，不可有两个相同路径的目录，如果有的话，就要进行冲突处理，最多只能保留一个
# TODO: 如果任务数比较多的话，而且没有-nm，可以先一次性把所有星标完整拉取一次，以后就不需要每次都检查，相当于是退化为-nm

# TODO: 重要，全量拉取使用 10 并发，但是一旦 count 变了，可能要全部直接取消，然后重新跑
# TODO: 先拉取一次 115 的更新事件，这个事件从数据库中最新一条数据的更新事件开始，如果没有数据，则为当前（不需要立即拉一次），以后轮到下一个任务时，只需要在最近一次拉取时间之后进行拉取，如果事件发生时间在当前记录的更新时间之前，则忽略此事件

# TODO: 移除创建时间 ctime，增加文件是否违规标记 is_collect
