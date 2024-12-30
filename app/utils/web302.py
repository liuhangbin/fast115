#!/usr/bin/env python3
# vim: sts=4 ts=4 sw=4 expandtab :
# encoding: utf-8

from collections.abc import Mapping
from time import time
from urllib.parse import parse_qsl, unquote, urlsplit

from cachedict import LRUDict, TTLDict
from p115client import check_response, P115Client

SHA1_TO_PICKCODE: LRUDict[str, str] = LRUDict(65536)
DOWNLOAD_URL_CACHE: TTLDict[str | tuple[str, int], str] = TTLDict(65536, 3600)
DOWNLOAD_URL_CACHE2: LRUDict[tuple[str, str], tuple[str, int]] = LRUDict(1024)

def get_pickcode_for_sha1(client: P115Client, sha1: str) -> str:
    if pickcode := SHA1_TO_PICKCODE.get(sha1, ""):
        return pickcode
    resp = client.fs_shasearch(sha1, async_=True)
    check_response(resp)
    pickcode = SHA1_TO_PICKCODE[sha1] = resp["data"]["pick_code"]
    return pickcode

def get_downurl(
    client: P115Client,
    pickcode: str,
    user_agent: str = "",
    app: str = "android",
) -> str:
    if url := DOWNLOAD_URL_CACHE.get(pickcode, ""):
        return url
    elif pairs := DOWNLOAD_URL_CACHE2.get((pickcode, user_agent)):
        url, expire_ts = pairs
        if expire_ts >= time():
            return url
        DOWNLOAD_URL_CACHE2.pop((pickcode, user_agent))
    url = client.download_url(pickcode, headers={"User-Agent": user_agent}, app=app or "android", async_=True)
    if "&c=0&f=&" in url:
        DOWNLOAD_URL_CACHE[pickcode] = url
    elif "&c=0&f=1&" in url:
        expire_ts = int(next(v for k, v in parse_qsl(urlsplit(url).query) if k == "t"))
        DOWNLOAD_URL_CACHE2[(pickcode, user_agent)] = (url, expire_ts - 60)
    return url

def find_query_value(query, key):
    index = query.find(key+"=")
    if index >= 0:
        start = index + len(key) + 1
        stop = query.find("&", start)
        if stop == -1:
            return query[start:].strip()
        else:
            return query[start:stop].strip()
    return ""
