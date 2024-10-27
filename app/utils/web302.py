#!/usr/bin/env python3
# vim: sts=4 ts=4 sw=4 expandtab :
# encoding: utf-8
from urllib3 import PoolManager
from base64 import b64decode, b64encode
from functools import partial
from string import hexdigits
try:
    from orjson import loads
except ImportError:
    from json import loads

G_kts = b"\xf0\xe5i\xae\xbf\xdc\xbf\x8a\x1aE\xe8\xbe}\xa6s\xb8\xde\x8f\xe7\xc4E\xda\x86\xc4\x9bd\x8b\x14j\xb4\xf1\xaa8\x015\x9e&i,\x86\x00kO\xa564b\xa6*\x96h\x18\xf2J\xfd\xbdk\x97\x8fM\x8f\x89\x13\xb7l\x8e\x93\xed\x0e\rH>\xd7/\x88\xd8\xfe\xfe~\x86P\x95O\xd1\xeb\x83&4\xdbf{\x9c~\x9dz\x812\xea\xb63\xde:\xa9Y4f;\xaa\xba\x81`H\xb9\xd5\x81\x9c\xf8l\x84w\xffTx&_\xbe\xe8\x1e6\x9f4\x80\\E,\x9bv\xd5\x1b\x8f\xcc\xc3\xb8\xf5"
RSA_e = 0x8686980c0f5a24c4b9d43020cd2c22703ff3f450756529058b1cf88f09b8602136477198a6e2683149659bd122c33592fdb5ad47944ad1ea4d36c6b172aad6338c3bb6ac6227502d010993ac967d1aef00f0c8e038de2e4d3bc2ec368af2e9f10a6f1eda4f7262f136420c07c331b871bf139f74f3010e3c4fe57df3afb71683 
RSA_n = 0x10001
SHA1_TO_PICKCODE = {} # type: dict[str, str]

to_bytes = partial(int.to_bytes, byteorder="big", signed=False)
from_bytes = partial(int.from_bytes, byteorder="big", signed=False)
urlopen = PoolManager(128).request

def acc_step(start, stop, step=1):
    for i in range(start + step, stop, step):
        yield start, i, step
        start = i
    if start != stop:
        yield start, stop, stop - start

def bytes_xor(v1, v2):
    return to_bytes(from_bytes(v1) ^ from_bytes(v2), len(v1))

def gen_key(rand_key, sk_len) -> bytearray:
    xor_key = bytearray()
    append = xor_key.append
    if rand_key and sk_len > 0:
        length = sk_len * (sk_len - 1)
        index = 0
        for i in range(sk_len):
            x = (rand_key[i] + G_kts[index]) & 0xff
            append(G_kts[length] ^ x)
            length -= sk_len
            index += sk_len
    return xor_key

def pad_pkcs1_v1_5(message):
    return from_bytes(b"\x00" + b"\x02" * (126 - len(message)) + b"\x00" + message)

def xor(src, key):
    src = memoryview(src)
    key = memoryview(key)
    secret = bytearray()
    i = len(src) & 0b11
    if i:
        secret += bytes_xor(src[:i], key[:i])
    for i, j, s in acc_step(i, len(src), len(key)):
        secret += bytes_xor(src[i:j], key[:s])
    return secret

def encrypt(data):
    "RSA 加密"
    xor_text = bytearray(16)
    tmp = memoryview(xor(data, b"\x8d\xa5\xa5\x8d"))[::-1]
    xor_text += xor(tmp, b"x\x06\xadL3\x86]\x18L\x01?F")
    cipher_data = bytearray()
    view = memoryview(xor_text)
    for l, r, _ in acc_step(0, len(view), 117):
        p = pow(pad_pkcs1_v1_5(view[l:r]), RSA_n, RSA_e)
        cipher_data += to_bytes(p, (p.bit_length() + 0b111) >> 3)
    return b64encode(cipher_data)

def decrypt(cipher_data):
    "RSA 解密"
    cipher_data = memoryview(b64decode(cipher_data))
    data = bytearray()
    for l, r, _ in acc_step(0, len(cipher_data), 128):
        p = pow(from_bytes(cipher_data[l:r]), RSA_n, RSA_e)
        b = to_bytes(p, (p.bit_length() + 0b111) >> 3)
        data += memoryview(b)[b.index(0)+1:]
    m = memoryview(data)
    key_l = gen_key(m[:16], 12)
    tmp = memoryview(xor(m[16:], key_l))[::-1]
    return xor(tmp, b"\x8d\xa5\xa5\x8d")

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

def get_pickcode_for_sha1(cookies, sha1):
    pickcode = SHA1_TO_PICKCODE.get(sha1)
    if pickcode:
        return pickcode
    resp = urlopen(
        "GET", 
        f"https://webapi.115.com/files/shasearch?sha1={sha1}", 
        headers={"Cookie": cookies}, 
    ).json()
    if resp["state"]:
        info = resp["data"]
        pickcode = SHA1_TO_PICKCODE[sha1] = info["pick_code"]
        return pickcode

def get_downurl(cookies, pickcode, user_agent = ""):
    """获取文件的下载链接
    """
    resp = urlopen(
        "POST", 
        "https://proapi.115.com/app/chrome/downurl", 
        fields={"data": encrypt(b'{"pickcode":"%s"}' % bytes(pickcode, "ascii")).decode("ascii")}, 
        headers={"Cookie": cookies, "User-Agent": user_agent}, 
    ).json()
    if resp["state"]:
        resp["data"] = loads(decrypt(resp["data"]))
    return resp
