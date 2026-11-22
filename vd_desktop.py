#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
视频下载器 · 电脑版
Cangora 制作

- 粘贴分享文案自动只取链接，支持一次多条
- 可自定义保存目录（会记住上次的目录）
- 每条任务独立进度条和状态
- 内置 yt-dlp，自动识别 ffmpeg（同目录 ffmpeg.exe 或 PATH）
"""

import os
import re
import sys
import time
import json
import queue
import threading
import shutil
import subprocess
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

APP_NAME = "视频下载器"
APP_VER = "1.9.11"
APP_VC = 21
UPDATE_MANIFEST = "https://raw.githubusercontent.com/jx0529/vd-desktop/main/update.json"
APP_EXE_NAME = "视频下载器.exe"
SUB_DIR = "视频下载器"  # 下载文件统一收进这个子文件夹
UPDATE_EXE = "https://github.com/jx0529/vd-desktop/releases/latest/download/VideoDownloader.exe"
AUTHOR = "Cangora 制作"

THIS_VERSION_NOTES = (
    "1. 登录一次就记住：Cookie 存本机，重启或更新后不用再登录\n"
    "2. 识别时会自动带上保存的登录态，抖音标题/作者能正常取到\n"
    "3. 更新进度多了剩余时间，显示成「剩 1:20」\n"
    "4. 更新失败可直接用浏览器下载最新版\n"
    "5. 更新包 CDN 优先 + 6 段并行，速度快很多\n"
    "6. 右侧滚动条改成深色，和背景一致，不再突兀\n"
    "7. 启动时预先加载解析组件，运行中不再报 base_library.zip 找不到\n"
    "8. 登录窗口启动前强清占用配置的浏览器进程，减少「退出码 21」\n"
    "9. 画质 / Cookie 下拉框改成深色，不再出现白色块"
)

# ---------- 抖音：用本机浏览器（CDP）在登录态里取视频信息 ----------
import socket
import base64
import struct
import urllib.request
import urllib.error

def _log_dir():
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "VDDesktop")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


# 日志放用户数据目录，不写在程序旁边（桌面），避免留垃圾文件
LOG_PATH = os.path.join(_log_dir(), "vd_log.txt")


def dlog(msg):
    """把关键步骤写进日志文件，出问题时用户直接把文件发过来"""
    try:
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > 3 * 1024 * 1024:
            os.remove(LOG_PATH)
        line = "%s  %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def _range_probe(url, timeout=12):
    """探测文件总大小，支持返回 (total, accept_ranges)"""
    import urllib.request
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "VDDesktop",
                                                   "Range": "bytes=0-1"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            code = getattr(r, "status", 200) or 200
            cr = r.headers.get("Content-Range") or ""
            m = re.search(r"/(\d+)$", cr)
            total = int(m.group(1)) if m else int(r.headers.get("Content-Length") or 0)
            return total, (code == 206)
    except Exception:
        return 0, False


def fast_download(url, dest, emit, threads=6, timeout=20):
    """多线程分段下载（支持就并行，不支持就单流），emit(已下载字节, 总字节, 速度MB/s)"""
    import urllib.request
    total, ranged = _range_probe(url)
    if total > 4 * 1024 * 1024 and ranged:
        n = min(threads, max(2, total // (4 * 1024 * 1024)))
        step = total // n + 1
        parts = [dest + ".p%d" % i for i in range(n)]
        done = {}
        lock = threading.Lock()
        err = []

        def grab(i, start, end):
            try:
                req = urllib.request.Request(url, headers={
                    "User-Agent": "VDDesktop",
                    "Range": "bytes=%d-%d" % (start, end)})
                with urllib.request.urlopen(req, timeout=timeout) as r, open(parts[i], "wb") as f:
                    while True:
                        buf = r.read(262144)
                        if not buf:
                            break
                        f.write(buf)
                        with lock:
                            done[i] = done.get(i, 0) + len(buf)
            except Exception as e:
                err.append(str(e))

        ts = []
        for i in range(n):
            st = i * step
            en = min(total - 1, st + step - 1)
            t = threading.Thread(target=grab, args=(i, st, en), daemon=True)
            t.start()
            ts.append(t)
        t0 = time.time()
        last = 0.0
        while any(t.is_alive() for t in ts):
            time.sleep(0.3)
            with lock:
                got = sum(done.values())
            now = time.time()
            if now - last >= 0.4:
                last = now
                sp = got / max(0.1, now - t0) / 1048576.0
                emit(got, total, sp)
            if err:
                break
        for t in ts:
            t.join(timeout=5)
        if not err:
            with open(dest, "wb") as out:
                for p_ in parts:
                    with open(p_, "rb") as f:
                        while True:
                            buf = f.read(1048576)
                            if not buf:
                                break
                            out.write(buf)
            for p_ in parts:
                try:
                    os.remove(p_)
                except Exception:
                    pass
            size = os.path.getsize(dest)
            if size >= total - 65536:
                emit(size, total, size / max(0.1, time.time() - t0) / 1048576.0)
                return size
        for p_ in parts:
            try:
                os.remove(p_)
            except Exception:
                pass
    # 单流兜底
    got = 0
    t0 = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": "VDDesktop"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        if not total:
            total = int(r.headers.get("Content-Length") or 0)
        with open(dest, "wb") as f:
            last = 0.0
            while True:
                buf = r.read(262144)
                if not buf:
                    break
                f.write(buf)
                got += len(buf)
                now = time.time()
                if now - last >= 0.4:
                    last = now
                    emit(got, total, got / max(0.1, now - t0) / 1048576.0)
    return got


DOUYIN_ID_RE = re.compile(r'/(?:video|note|share/video)/(\d{15,})')
DOUYIN_SHORT_RE = re.compile(r'v\.douyin\.com/([A-Za-z0-9_\-]+)')


def douyin_id_of(url):
    """从各种抖音链接里取 aweme_id（短链会跟随跳转）"""
    if not url:
        return ""
    m = DOUYIN_ID_RE.search(url)
    if m:
        return m.group(1)
    m = re.search(r'(?:modal_id|aweme_id|item_ids?)=([0-9]{15,})', url)
    if m:
        return m.group(1)
    if DOUYIN_SHORT_RE.search(url):
        got = _expand_short(url)
        if got:
            return got
    return ""


def _expand_short(url):
    """把 v.douyin.com/xxx 短链跟到最终视频页，取出 aweme_id"""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA_DESKTOP,
                                                   "Accept-Language": "zh-CN,zh;q=0.9"})
        with urllib.request.urlopen(req, timeout=10) as r:
            final = r.geturl()
            body = ""
            try:
                body = r.read(300000).decode("utf-8", "replace")
            except Exception:
                pass
    except Exception as e:
        dlog("短链跳转失败: %s" % e)
        return ""
    m = DOUYIN_ID_RE.search(final) or re.search(r'(?:modal_id|aweme_id|item_ids?)=([0-9]{15,})', final)
    if m:
        dlog("短链解析到 id " + m.group(1))
        return m.group(1)
    for pat in (r'/video/(\d{15,})',
                r'"aweme_id"\s*:\s*"?(\d{15,})',
                r'aweme_id=(\d{15,})',
                r'"itemId"\s*:\s*"?(\d{15,})'):
        m = re.search(pat, body)
        if m:
            dlog("短链页面解析到 id " + m.group(1))
            return m.group(1)
    dlog("短链没有解析出 id: " + final[:100])
    return ""


def _ws_send(sock, obj):
    data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    n = len(data)
    head = bytearray([0x81])
    if n < 126:
        head.append(0x80 | n)
    elif n < 65536:
        head.append(0x80 | 126)
        head += struct.pack("!H", n)
    else:
        head.append(0x80 | 127)
        head += struct.pack("!Q", n)
    mask = os.urandom(4)
    head += mask
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    sock.sendall(bytes(head) + masked)


def _ws_recv(sock):
    buf = b""

    def read(n):
        nonlocal buf
        while len(buf) < n:
            chunk = sock.recv(max(1, n - len(buf)))
            if not chunk:
                raise EOFError("ws closed")
            buf += chunk
        out, buf = buf[:n], buf[n:]
        return out

    payload = b""
    while True:
        h = read(2)
        b1, b2 = h[0], h[1]
        fin = b1 & 0x80
        op = b1 & 0x0f
        ln = b2 & 0x7f
        if ln == 126:
            ln = struct.unpack("!H", read(2))[0]
        elif ln == 127:
            ln = struct.unpack("!Q", read(8))[0]
        if b2 & 0x80:
            read(4)
        payload += read(ln)
        if fin:
            break
    if op == 0x8:
        raise EOFError("ws closed")
    try:
        return json.loads(payload.decode("utf-8", "replace"))
    except Exception:
        return {}


def cdp_ws_connect(ws_url, timeout=10):
    m = re.match(r'ws://([^/:]+):(\d+)(/.*)', ws_url)
    if not m:
        raise RuntimeError("bad ws url: " + ws_url)
    host, port, path = m.group(1), int(m.group(2)), m.group(3)
    sock = socket.create_connection((host, port), timeout)
    key = base64.b64encode(os.urandom(16)).decode()
    req = ("GET %s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\n"
           "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
           "Sec-WebSocket-Version: 13\r\n\r\n") % (path, host, port, key)
    sock.sendall(req.encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise RuntimeError("ws handshake failed")
        buf += chunk
    return sock


DETAIL_JS = """(async () => {
  try {
    const u = "/aweme/v1/web/aweme/detail/?device_platform=webapp&aid=6383&channel=channel_pc_web"
      + "&aweme_id=__ID__&pc_client_type=1&version_code=170400&version_name=17.4.0"
      + "&cookie_enabled=true&platform=PC";
    let j = null, code = "";
    for (let i = 0; i < 4; i++) {
      try {
        const r = await fetch(u, {credentials: "include"});
        if (r.ok) {
          j = await r.json();
          if (j && j.aweme_detail) break;
          code = "空数据";
        } else {
          code = "HTTP " + r.status;
        }
      } catch (e) { code = String(e); }
      await new Promise(r => setTimeout(r, 2000));
    }
    if (!j || !j.aweme_detail) return JSON.stringify({ok: false, code: code || "无返回"});
    const d = j.aweme_detail || {};
    const v = d.video || {};
    let purl = "";
    if (v.play_addr && v.play_addr.url_list && v.play_addr.url_list.length)
      purl = v.play_addr.url_list[0];
    else if (v.play_addr_h264 && v.play_addr_h264.url_list && v.play_addr_h264.url_list.length)
      purl = v.play_addr_h264.url_list[0];
    else if (v.play_addr_265 && v.play_addr_265.url_list && v.play_addr_265.url_list.length)
      purl = v.play_addr_265.url_list[0];
    return JSON.stringify({
      ok: true,
      title: d.desc || "",
      author: (d.author || {}).nickname || "",
      dur: (v.duration || 0),
      like: ((d.statistics || {}).digg_count || 0),
      play: purl
    });
  } catch (e) { return JSON.stringify({ok: false, code: String(e)}); }
})()"""


def _free_port(start=9333, end=9400):
    for p in range(start, end):
        try:
            sk = socket.socket()
            sk.bind(("127.0.0.1", p))
            sk.close()
            return p
        except Exception:
            continue
    return start


def _clear_profile_locks(prof):
    """清掉上次浏览器没退干净留下的锁文件"""
    for rel in ("SingletonLock", "SingletonCookie", "SingletonSocket",
                "lockfile", os.path.join("Default", "LOCK")):
        try:
            p = os.path.join(prof, rel)
            if os.path.lexists(p):
                os.remove(p)
                dlog("已清理锁文件 " + rel)
        except Exception:
            pass


def cookies_store_path():
    """登录 Cookie 的持久化文件（跟程序无关，更新/重启都不会丢）"""
    return os.path.join(_log_dir(), "cookies.json")


def _browser_ws(port, timeout=3):
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:%d/json/version" % port, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace")).get("webSocketDebuggerUrl")
    except Exception:
        return None


def _ws_call(ws_url, method, params=None, tries=60):
    sock = None
    try:
        sock = cdp_ws_connect(ws_url)
    except Exception as e:
        dlog("连调试端口失败: %s" % e)
        return None
    try:
        _ws_send(sock, {"id": 1, "method": method, "params": params or {}})
        for _ in range(tries):
            try:
                msg = _ws_recv(sock)
            except Exception:
                return None
            if msg.get("id") == 1:
                return msg
        return None
    finally:
        try:
            sock.close()
        except Exception:
            pass


def _cdp_targets(port):
    """浏览器级 + 页面级调试地址，哪个能用就用哪个"""
    out = []
    b = _browser_ws(port)
    if b:
        out.append(b)
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:%d/json/list" % port, timeout=3) as r:
            for t in json.loads(r.read().decode("utf-8", "replace")):
                u = t.get("webSocketDebuggerUrl")
                if u:
                    out.append(u)
    except Exception:
        pass
    return out


def save_cookies_from_cdp(port):
    """从浏览器把当前 Cookie（含登录态）存到本地 JSON，供以后直接用"""
    keep = []
    for tgt in _cdp_targets(port):
        for method in ("Network.getAllCookies", "Storage.getCookies"):
            msg = _ws_call(tgt, method)
            cks = ((msg or {}).get("result") or {}).get("cookies") or []
            if cks:
                for c in cks:
                    dom = (c.get("domain") or "").lstrip(".")
                    if any(k in dom for k in ("douyin", "bytedance", "toutiao", "ixigua", "bilibili")):
                        keep.append(c)
                if keep:
                    break
        if keep:
            break
    if not keep:
        dlog("浏览器没返回可用的 Cookie")
        return 0
    try:
        with open(cookies_store_path(), "w", encoding="utf-8") as f:
            json.dump(keep, f, ensure_ascii=False)
        dlog("已保存 %d 条 Cookie 到 %s" % (len(keep), cookies_store_path()))
        return len(keep)
    except Exception as e:
        dlog("写 Cookie 文件失败: %s" % e)
        return 0


def load_cookies():
    try:
        with open(cookies_store_path(), "r", encoding="utf-8") as f:
            cks = json.load(f)
        return [c for c in cks if c.get("name") and c.get("domain")] if cks else []
    except Exception:
        return []


def write_netscape_cookies(cks, path):
    """把 JSON Cookie 转成 yt-dlp 能读的 cookies.txt"""
    try:
        lines = ["# Netscape HTTP Cookie File"]
        for c in cks:
            dom = c.get("domain") or ""
            flag = "TRUE" if dom.startswith(".") else "FALSE"
            path_ = c.get("path") or "/"
            secure = "TRUE" if c.get("secure") else "FALSE"
            exp = c.get("expires")
            exp = int(exp) if isinstance(exp, (int, float)) and exp > 0 else 0
            lines.append("\t".join([dom, flag, path_, secure, str(exp), c.get("name", ""),
                                    c.get("value", "")]))
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return True
    except Exception as e:
        dlog("写 cookies.txt 失败: %s" % e)
        return False


def _cookies_db_path(prof):
    """Edge/Chrome 的 Cookie 数据库位置（新版在 Default\\Network\\Cookies）"""
    for rel in (os.path.join("Default", "Network", "Cookies"),
                os.path.join("Default", "Cookies"),
                "Network\\Cookies", "Cookies"):
        p = os.path.join(prof, rel)
        try:
            if os.path.isfile(p):
                return p
        except Exception:
            pass
    return None


def _kill_browser_on_profile(prof):
    """Windows：干掉还在占着这个配置目录的浏览器进程"""
    if sys.platform != "win32":
        return
    try:
        import subprocess as sp
        out = sp.run(["wmic", "process", "where",
                      "name='msedge.exe' or name='chrome.exe'",
                      "get", "processid,commandline", "/format:list"],
                     stdout=sp.PIPE, stderr=sp.DEVNULL, timeout=12)
        text = (out.stdout or b"").decode("utf-8", "replace")
        cur = os.path.basename(os.path.abspath(prof)).lower()
        pids = []
        block = {}
        for line in text.splitlines():
            line = line.strip()
            if not line:
                if block.get("commandline") and cur in block.get("commandline", "").lower():
                    pids.append(block.get("processid"))
                block = {}
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                block[k.lower()] = v.strip()
        for pid in pids:
            if pid and pid.isdigit():
                sp.run(["taskkill", "/F", "/T", "/PID", pid],
                       stdout=sp.DEVNULL, stderr=sp.DEVNULL)
                dlog("已结束占用配置的浏览器进程 " + pid)
    except Exception as e:
        dlog("清理浏览器进程失败: %s" % e)
    _kill_by_cmdline(os.path.basename(os.path.abspath(prof)))


def _kill_by_cmdline(kw):
    """PowerShell 按命令行关键字强杀（wmic 在新系统上常不可用）"""
    if sys.platform != "win32" or not kw:
        return
    try:
        import subprocess as sp
        cmd = ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*%s*' "
               "-and ($_.Name -eq 'msedge.exe' -or $_.Name -eq 'chrome.exe') } | "
               "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }") % kw
        sp.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
               stdout=sp.DEVNULL, stderr=sp.DEVNULL, timeout=20,
               creationflags=0x08000000)
        dlog("已按命令行清理占用 %s 的浏览器" % kw)
    except Exception as e:
        dlog("PowerShell 清理失败: %s" % e)


def _browser_args(exe, prof, port, url, mode):
    args = [exe,
            "--remote-debugging-port=%d" % port,
            "--user-data-dir=" + prof,
            "--no-first-run", "--no-default-browser-check",
            "--disable-gpu", "--mute-audio", "--disable-extensions",
            "--disable-background-networking",
            "--disable-features=Translate,MediaRouter,OptimizationHints"]
    if mode == "headless":
        args.append("--headless=new")
    else:
        args += ["--window-position=-2400,-2400", "--window-size=1280,900"]
    args.append(url)
    return args


def _cdp_once(exe, prof, page, aid, timeout, mode):
    port = _free_port()
    _clear_profile_locks(prof)
    _kill_browser_on_profile(prof)
    creationflags = 0x08000000 if sys.platform == "win32" else 0
    args = _browser_args(exe, prof, port, page, mode)
    dlog("启动浏览器(%s) 端口 %d: %s" % (mode, port, " ".join(args[:3])))
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=creationflags)
    sock = None
    try:
        ws = None
        deadline = time.time() + 40
        while time.time() < deadline:
            if proc.poll() is not None:
                raise RuntimeError("浏览器启动后立刻退出了（退出码 %s）" % proc.returncode)
            try:
                with urllib.request.urlopen("http://127.0.0.1:%d/json" % port, timeout=2) as r:
                    tabs = json.loads(r.read().decode("utf-8", "replace"))
                for t in tabs:
                    if t.get("type") == "page" and "douyin.com" in (t.get("url") or ""):
                        ws = t.get("webSocketDebuggerUrl")
                        break
                if not ws and tabs:
                    for t in tabs:
                        if t.get("type") == "page":
                            ws = t.get("webSocketDebuggerUrl")
                            break
            except Exception:
                pass
            if ws:
                break
            time.sleep(0.5)
        if not ws:
            raise RuntimeError("浏览器调试端口没起来（可能被杀毒软件拦了）")
        dlog("拿到调试地址 " + ws.split("/")[-1])
        sock = cdp_ws_connect(ws)
        _ws_send(sock, {"id": 1, "method": "Runtime.enable"})
        _ws_recv(sock)
        saved = load_cookies()
        if saved:
            try:
                _ws_send(sock, {"id": 4, "method": "Network.setCookies",
                                "params": {"cookies": saved}})
                dlog("已注入 %d 条本地 Cookie" % len(saved))
            except Exception as e:
                dlog("注入 Cookie 失败: %s" % e)
        # 等页面真正加载完、ttwid 落到 Cookie 里（这一步以前只等 1.5 秒，网络慢就 403）
        ready = False
        end = time.time() + 20
        while time.time() < end:
            _ws_send(sock, {"id": 3, "method": "Runtime.evaluate", "params": {
                "expression": "document.readyState + '|' + (document.cookie.indexOf('ttwid')>=0 ? '1' : '0')",
                "returnByValue": True}})
            msg = _ws_recv(sock)
            val = (((msg.get("result") or {}).get("result") or {}).get("value")) or ""
            if "complete" in val and val.endswith("|1"):
                ready = True
                break
            if "complete" in val and time.time() > end - 8:
                ready = True
                break
            time.sleep(0.4)
        dlog("页面就绪=%s" % ready)
        time.sleep(1.0)
        data = None
        result = ""
        for attempt in range(3):
            _ws_send(sock, {"id": 2, "method": "Runtime.evaluate", "params": {
                "expression": DETAIL_JS.replace("__ID__", aid),
                "awaitPromise": True, "returnByValue": True, "userGesture": True}})
            result, end = "", time.time() + timeout
            while time.time() < end:
                msg = _ws_recv(sock)
                if msg.get("id") == 2:
                    result = (((msg.get("result") or {}).get("result") or {}).get("value")) or ""
                    break
            dlog("页面返回 %d 字节（第%d次）" % (len(result or ""), attempt + 1))
            if not result:
                time.sleep(2)
                continue
            try:
                data = json.loads(result)
            except Exception:
                time.sleep(2)
                continue
            if data.get("ok") and data.get("play"):
                break
            if data.get("title"):
                return data
            dlog("抖音返回 %s，重试" % (data.get("code") or "空数据"))
            time.sleep(2)
        if not data:
            raise RuntimeError("页面没有返回数据")
        if not data.get("ok"):
            if data.get("title"):
                return data
            raise RuntimeError("抖音返回 %s" % (data.get("code") or "空数据"))
        if not data.get("play"):
            raise RuntimeError("没找到播放地址")
        return data
    finally:
        try:
            if sock:
                sock.close()
        except Exception:
            pass
        try:
            if proc.poll() is None:
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    proc.terminate()
                time.sleep(0.6)
        except Exception:
            pass


def resolve_douyin_cdp(exe, prof, video_url, port=9333, timeout=45):
    """用独立登录配置的浏览器，在真实页面里请求详情接口取标题/作者/时长/直链"""
    aid = douyin_id_of(video_url)
    if not aid:
        raise RuntimeError("抖音链接里没有视频 id")
    page = "https://www.douyin.com/video/" + aid
    dlog("开始解析抖音 %s → %s" % (video_url, aid))
    last = "未知错误"
    attempts = [("headless", prof), ("window", prof)]
    try:
        import tempfile
        fresh = os.path.join(tempfile.gettempdir(), "vd_dy_fresh")
        try:
            if os.path.isdir(fresh):
                shutil.rmtree(fresh, ignore_errors=True)
        except Exception:
            pass
        attempts.append(("headless", fresh))
    except Exception:
        pass
    for mode, p in attempts:
        try:
            data = _cdp_once(exe, p, page, aid, timeout, mode)
            dlog("解析成功(%s/%s): %s" % (mode, os.path.basename(p), json.dumps(
                {k: data.get(k) for k in ("title", "author", "dur")}, ensure_ascii=False)[:180]))
            return data
        except Exception as e:
            last = str(e)
            dlog("解析失败(%s %s): %s" % (mode, os.path.basename(p), last))
            time.sleep(1.0)
    raise RuntimeError(last)

BG = "#0d1117"
CARD = "#161b22"
FG = "#e6edf3"
SUB = "#8b949e"
BLUE = "#2f81f7"
GREEN = "#3fb950"
RED = "#e5534b"

ICON_B64 = ("iVBORw0KGgoAAAANSUhEUgAAAEgAAABICAYAAABV7bNHAAAd7UlEQVR42s18baydV3Xms9be77n3"
              "3E87dmyHOuQDYZJQ0SbFtBERNNMGBIVW7XCjUVGrEQmaqWibtqj90Qq5blWNRoiqgn5oBPSDAG3t"
              "qmpLgIEkhDAJIVRMaGnsEIID2M6Xaye+uV/nnHfv1R97rbX3uXaqmV8zVzq+1/ece877rnd9POtZ"
              "z3oJL/1Fb3zjoXD//Yd7ALjq7R/eOyvxTYHzLfPzg+tnZ+LlzLSUMyiLACIAAPuXCAAI0O9ib6rP"
              "CwEMQEh/Q/YPABIIAKLyEJT/EFH9mcvLCQQRATFQ/kF5YyaEQAhMkgSrG+PJybWt/hEJ8e6tUf/5"
              "Jz/6s88CwBsPHYr3Hz6c/NC3G+Gipjl0iHH4cAaAa3/iT18pgX9JQCvEcR+IIFkw6XtITgAIzPY2"
              "Uj+FBBCaPkFIsSMBZH9jf0oESFarlHdis5C9jqjYkMr7ZgCkrwcTBAICgbi8TvRziANCxwixAyRD"
              "cv+MBDpKwIeOf/Ad39p+zv++gVaOBBy9NV133aFB/4or3wei94Y4N5Q0huQ+CyBMIBCR1MMvBwpA"
              "RPRdGSJZjUfuWZDyVxTKSZazKIYVCMgMSaQekYvt1IOIqxcJsr5XqJfHXsPlvYSghwoRIiES4hBY"
              "ZuaQxpubGfkD8fQ//+6xo4fHdu4vbSB9wYFbPngVZhc+EWcWbsyTNaSMnkCBuBw+IBBR1yYBEevJ"
              "pHLCAjCX5wUAOJQrLuJewqGGn4Vc+UkNp9FSwqd6Erm3qMGZy8O8NgT1Mg1z9ULRvwMJ1FiJmGKY"
              "nUc/2XhINl985+N/+PNPbjcSbQ+rA2/64DUYLN4dupn9uR9NiBAFINHYCCFAJJcDlBI+3FxtyaLR"
              "QM2JkIeQAKDAeqZSfIuKY7GGBmu4SAZE/dRDiTD1fyEqLk0AkIHAEA7l4EjUm9jPVqBhRyXghaWP"
              "s3NdTuNTabR2y+O//87HcEgYhynDr5Ma58q3fuQKDBbu4dDtT/1mT4xOQCR+9QQiufkk1KskouHF"
              "/nTxINEQEg8TEXEji4eGhaa+j1hGI70AUkOIAGaGMCGzeQo8fxHl4tlMyMya8+oDJAALKBARo0uT"
              "tZ4HYX+Yn7/nyt/6+BU4TBmHDrGnPqwc4euAMFk//0CYWTiY+42eiKNd/RLCTVUSveQg/cDpGlBO"
              "jMtJuc3IT6AaohqY/OTKc54tPe+IeiVAzB42op4ItmNRz2Kuv7PYpKyfU96nhJ0ADGRIH+YXYtpa"
              "+8dA+aZjx5Bw9NbMKytHGEdvTeO1s++LM0sHc781AXHMbXoSKieoNZS2naSIvsaORg8AxOXkxPKA"
              "eiLraz35wn+eSuik5duszwwihoCQLacwlRzkcEH0swRCmvQZILZcVrxH7EGa/wLHPNqYdEuXHOwp"
              "vA9Hb00rR44wAUIH3v7hK0UGx4koEok6rSieIfMjCGo5BwHIFgRqDKKSB9hqLJA1WRE3JdkNYVWr"
              "nJAVapC+R5uUPcwJ0BwmrEFKrPlL3GhiWItr5SMq3iLq9cLTFxNMwjFmUOr7NLr28fetfIcBkpzo"
              "vTyYmzGnJ3P5Bt6JYxf1GuhBE8FOzXJEhYMAIXvQiaVlfVmMASEyOBJiVGAXM0JkxMCIMSB2ARyq"
              "1wqbkeuxeZhyqJWKqs+X0BVkBiRYCEoJVa2QmpsoYyI8tzBDFN8LkNCBt/2P3SmH46Eb7JKcinnI"
              "SmwGUSifwHqlJStoFscxWbJ6CGt1oAZT1zwEZggEkRmjScbmqC9X3fyJ4J5EVhkDMJwdYDgTkErJ"
              "1FCxDFoghDCQ2UCoeHk3QMqckQM8/MBWBa3iGmSA8KCD9P3ZtLl+bUwTvCXMDHdLmmRirYdZ41/r"
              "b8FyVIEeaoigQbzVOE3OlqboiSAExvrWBFftG+Km11yBENgxUJv2SL0kZcFDx57Ft85sYX4Yp7yo"
              "hGaGsCFseMjaybv3xIqt0KBv98xQjMSBCZIyD4e7E/VviQjxzVrJvSA4RuFQQwy5iTjFNAJ3ZdLk"
              "6kiaqn0EpLmUMJ5kXLF3Dn/z396C3TsX8H/y9cKL67j1dz+Hb5/ZwnC206AtYZLsfLlGNzmeKkaR"
              "IOqppInfcph6LAMI6m1MEJBwDMKxezMDcr2WoILZPP+wVijyRCyWnFERqld9zS0gURRdjFaqleIZ"
              "ZqxtjvFjr/0+7N65gNE4oe+3P3L5nspjNE7YsTiPN7/2cqyPJuDAHkZCAGvL4oelJ0kBQACEi4eB"
              "A4i5AEtogvYH1/JPBAQikUSgfH1kyH7JPYiEibjxAFagp57DhmtkOgmKJe2Sk1ImrG2OMYjA1jhj"
              "OBxg0DGyUAV5VDwtMBACufcZI9CyAAYcufEIUMMOsHX8Ws6DlX9BVvBZgSQcS5VSD69+xNT0ceAs"
              "PYh5f2SmpSxUy7mX5tzAvpKLSvJktOlFvN/Qg5SEX/yZV+GHrtmDr/7L0/jLe7+NM+dH2LE0LBSF"
              "4ePmJKeTFmrf1TTC+unOiIAIbAZgxVuKcTKnAihbMKm5CKGEFlEJe2F2sOvvEYozMGgpiihdQRlS"
              "GBr4xfQwsmaJpttbu+LaL66ujfGTN+3Hu972apxbHeNVb92F/3Dwcnzss8fx6YdPY7OPBZe0DMYU"
              "tqJt1qofRuo9pBiLDDiwdeyilYqmOn1m9mQtwSinctHFWg89r8zioFKIIWBwFkGeSsDs7iiOCcXb"
              "BZFpLsAaP4CQRbB31zxe3OyxtjnCmRc2sbgwh9/4uR/Gh+54PW64egnnVjcwnvRN8m/xk5mEpkg2"
              "/xVb5SmeghgU6AkoNCFlr7OQDAQJtW1BYFAAOBSAiUDFsCyOi4RLEYhNA+RGIke54kbzvpykDWjt"
              "UzNYwVafMjKoeCMRNkc91rd6HLhqD37/jr143T3HsTgMTSRVk8hUpMmFTR6VLq30UJr8KQMd6xWv"
              "SZqhhmGBUEam2jdy08BCW5BilwIcJbAj/tiWdrFO2g++8SaZPiHjOvzwNQ+ASu+VsvlAObDzayOE"
              "GLDyY6/G0lxA3yeEwLW7nzKHbPMmrZQWDpI1ZAQUikGytzINw+jho16n35kLRZKtxLOAKBSg6V0/"
              "gTIQpXF0zwpWGSyeRMPBkrcDjpo7qMU+YlhTTZQ0AQrw/ItbmJmZw6TPmPQJA203WmPTVKPsPXIN"
              "GYHRFd6VU+MhogYw75CG4mVmJfG0cqkPiCJpCsoW6O+jn7yWaWq546bMUpNZncLQysVcjQoASTKy"
              "SDGQhikrr8N+gQlJgNE4IaSMrgvl4EWaz5HGm8irkWgpJ9TG1Dwksyjo06hoWhPWViZTyT/ExiiI"
              "d/lg1upYQi16tGuecZqUmjTQ9ErUsntOmoeSIPVPUi4ggdXQTG2toungYUIWYDTq0XVcGEsvDi0C"
              "BFjBn11eOx5jO3IQNQgaz4KX/Ez1eaLqZQz9WUNQ1LOQgehh4u+2nRxDbRwtaTu3S978kVaD0pBy"
              "4YAqBGw6fvJGlypXAhAjJQFTLlf2YtAoKtkvSuprSUZgxUBAsHbBqp16iBmGWdG/gUU7tWDUV3Zm"
              "oISYlUYfyRQMUcKgoU+RvatXSKiJDu7ecLLcKFLy5B8sZUvLFNY8E0K5ZDkLwna6AtNJl4QqRRuM"
              "umjYTfMSZmQj3gxQUqU8jLO2plWosgCsuSx65vduPWsnX0udBYV4aLHGsjg5j+Y7acm09oGINCeh"
              "olbLtcTgAPSpx6RP6GIEizThVb2WLG9IARISUJhFQ8FcCTXnngP7OYFZG1QFtzp+slkbuKQZVtCI"
              "rDiIaXrwZ8YRtMm5mWwiQxrvIqtioeCIDCCJIDTcEBHD2BQL28CElDLWNkbISTCIcbq0Y4r1nULy"
              "ErjxHHJ2kAJrv2a9ls9O9Pf6HAmyehSp8agl/jWNRPtwwz3s493acrRkeCFVy0SBKTtrKMRO0Wap"
              "B2UfygTkXKrV3OwMAMH6xhijyQTMjEEMiIHBDd9NTupUbCqcFRMZPVG8mUmBoYFCYw6tEFCpdCGU"
              "PJRtVgbx/CoaNAhqBwFYRCBZexOymK2gS6heSQGQtBp4E9vkMKvzzKwNJHtzKwAuWZpDZOCJU89h"
              "fXOMrfEEg9hhdtAhxlg6eyvffqEaFB0qb8NeFBTrxFA8OHI1Dtf+iwIpxiGtfOS5suQxfS5Uw9vw"
              "o0J8T7DUlPSSezJBE3JNWWJxq1VOUMIGVKpJmZ9nLAxncOnyHP75xNP4L398N/7moW9heX4GgxgR"
              "AyEGRhfIkfW24GoMJJ6sM4vjHw5cwpnNs9iTr425rb0QGxE1fVllF+sja46NsPlRg4pr7pHqph6X"
              "BEIseQhZ8YSS31zGy3MzAefXCHOzHRZmA544fRaf/NIxPPjN5/D8Vo+fODgEmBAjl6Rs5behSyEy"
              "zRw0SDpDygmzXbBcCHkqxmEqni4MhGBemKtgwrydK/vJWvozwY8nEyNakiMi5CzazBVDZKkl1t3R"
              "Sia4CQFBBmNudoAvfOM0bv7B/dh/6Q48dfY8/uxzx/GZR05iKxN2Lc9hHMaKcwhdDIiBapJvm+bt"
              "ZR6ErJNUBnvHnlinFZaLGEhc8EzgdlhJzp2LNbmW1AV1ns9Bk37xjIhGYlKTcS199qEC0Zwi2ypc"
              "5WQGg4Bnzk/wax99AFftnceJZ1dxZm2MxYVZzIQyBs7QHAE0OeciHbxsk1ZolTQvEMdFdYBoqg62"
              "3NdMU4kZzAXhZz0PwzrWiYIIocm/YCB6985UvYKbPIRUXBoF0nvXDylA0sUBhAzB7DBiMyV8/Xur"
              "GM5E7No5UyA+AI4BoVNlRztslJYsawn/mo+ICSEyQgzISBCi0nqYIZgKquZpT3c9AAl6L+EFNApX"
              "zkmo4KJMlZgjEGLDoFZhkqPlOuFwyO7EFkPNPSViyCieFDrGVp+RR32pEIHALFjrJ5ikNO0x1DCU"
              "TgtMC77GIthIPTZSwlhSYf1Kw4dBF9BxcK5cjNJQMVUOOrsjvahT0EbDDwoFHJrk4kFgBoK1EexA"
              "sPaUrBy+TEnhzNIQxUAG2oiw3idcMhdw4GULFb4rQf/8xgCX7ZhFlZY0A22aYptaehr7FmZw/b5l"
              "XDI/g16qOAoMPL2xhX/tJxjORJ+XuVcZYneMRT6P9yaYQtNmqBpFCwUdeMefC4WoSa7tk0RDoP4h"
              "2bSDq86mVDJ2GnPUZ1y9e4g/fc9NePmeZVyYTLYrQbazh1Ok9zQ3tF0Ppy9/+vwabv/8V/DY+ibm"
              "uk6leSaFEz22Ku1zGsRDS6qQgot+SZhAOYOJCBKs+1VsTnBFRh3zKt/AtW9x+VsAMmVwILw4muAn"
              "D+7Hy/fswKTMil0B0tA7jr3oAkWgXFQAJ/5HVAe8ACZJcNnyIt7xqsuxNh67WKGEdAGTFAxUFtwj"
              "muytuxelWSUowpbqhez4wXkT9kmlS1YUFcOZOuh4RfGHArQEwnDY4Z5jT2FrPEYMjJzzSznPVPd3"
              "cTeRVhY6RccCgixS+rmc8PnvPo1h19XWQkl3qDegHXxyI7dpJ7IkirhNbQJwdtktK90qoBB0gtlQ"
              "IG6c7Q+4AQWC+WHAw999Af/1o19C3/fl91JpMrrgRKkZUE+bSbWiNqSZMpgdFwP4tXu/gvueOYeF"
              "mQ59g4pbECzKRjh2YwYifALL3BJ74jQVlxDikoRVUORTSeigrcwSXEaXA1duxsipQEAEMgl2L83i"
              "048+h3d/9D43Um7mRRfXHtOUr8hFXa6U3qzVjgH8yr1fxiefPI3dC0MkrsM/I979pJvyXTBV8aZM"
              "lUsqeUqFV0ElQyCdV1v2djkug2LQMUhtEq35E2fuykwKQRy0JQL27BziU8eexbv/7EIjSfPPhcF1"
              "4bjH5RE29VXj3HHvl3HniVPYsziPpD0YgtKtyu2ARGdezfFqwWGjj5u+jQNrT1g6egZXt63oU5s4"
              "lqZ8AzmQ5iuphBTJ1ETDwq6XjH0753DX8Wfw7r9QI1ExEjUiiDYHyfTAeYpFAICca9/0K2qcfUsL"
              "2kST0h2VXUgEHQeVMCKyWZ8OspmmFHQuxCooTyfqWqlsPAJNuAUraHIO2vMoeGonlt7buFK1hKEE"
              "Rg/g0h0L+IfHnsXtd7ZGsmBp56jb888U06Ge0xrnNC5bWlDuJyrbCJcBVvxFlWhTcTk5us5O11Sz"
              "VXKuNL7caJpd44dGY1xAofg0I+sMCU67Zh+ZqBpEq0QmwoSAPTsX8anHn8Ptn7wPfSrhlholh1xk"
              "TaKdslpoMoBf/cKD+PiJk7hsab54js+yyBVupFIX7YSAAARWRpOt1VFVCmFKuMEqCGNTohRsoISR"
              "YhqzelavEcrgAFAQBZCNaMlinkq3bV01NXmqR8benQu469tncNtffxF934OJkZRbhi2kVL1I4zni"
              "jOQd9z6Ijz1xEvuWFtFzg8uAKVkeNeMe0UTNoWA24lYPAG89yGESVzEYMthUn9QYyvR+ibKyclQn"
              "BYELWW4zbV0FkEZmK8jInNXYhVyfsGDPznncdeJZ3H70i+hTD+ZQKRWiC8Iro0nIX3gQd544iX07"
              "FpGYwBzqBFiJOZA4VktcJzACcbLPnjfDechxozYx3hoENsFRDnWsYowdVBYTVBVqHsFcp6lTFU2v"
              "mr2XcEIO4rmql4y9ywv41HfO4La/vR+Tvvdp6rSqw8tGNc63T2Hf8iISh8KCcVXXO9m2DcySGixz"
              "MWCSrJpKDTcdKhTVSqm+Dl+ICx7MqCdWx7pSGUSlBYxrcSogABQJ3BEGEYixtCwUCRwJ0HBkq3Q6"
              "S+8J2LtjHp/+7hm86+/vx7ifzkmiIFCbFPzyF76Mj584hZctLRZQwEU4ZWOowvOU8mz5k2OhcWPg"
              "QpFoWJmyo4ycC7whG1BY2UfjWSJgcAKFrHob5XNtdGLJmKuSyzrzHsDqqMfq1gTnRxOsTRI4FAMX"
              "UWTpd9jm5OqBFICEjH07FvA/T57BbXdVT5pkvcqOcx7EJ548hb3Li+i9alaOmk3sZI12IHAM2JCM"
              "82mCF1KP85MxxpJdRGrJhi03kUzxCnUEXfivWBQSQZtVctzAPjgU74QNcfYAhoFx05WXOIl2bnML"
              "Xz+zis7WkXxXQjxPmH65QADBvqUFfObUGdz+mS/hI299AwYxOi90x70P4ONPni5hpdSoURBo1Bmi"
              "eY9CuRijlPAD83O4dGag6B/4p7U1bEAw4+sKhaRjAElndYGCAuZSGGwbMiqerjhIkyzZhME8R2EA"
              "B8LmVo/3/9QN+OkbXtHU5Yzb/up+3HXiOeyYGyDBEj578w/lkQwojgHsXVzEZ586h/901xfxnh+8"
              "BoPA+Mi/fBOfOfWsgkANH82iRFnFnOyrUBzKa9ZTwo3Li/jY6w76fB8AvvTsM7jt0W9ghoJPeRlU"
              "5S+m7gVA2Tj3UsmihVC5unUmVqX/jZZYk3EiQRcDsgj6VLr1QQxYec0V+IcnngLxTFXOK71pRqFg"
              "nFLhjhOAXQtzePj5F/GVe79cRtZMuHRxvgDVEKZGTUzNwDCUMClsBmGcevzMyy4DMWOci74g6AQ3"
              "2zjb6GM9x44Ygoyk+2psdAwTKFOZapSxjTSy2rpcS9vJ8VAgwGePn8TbXnNlmX/plXrdFfuwf3GA"
              "NSkTVAs1akZGrJiJXdhUPGvncBagWd31YtXwBPWEpELR8l7Z8ohqpAMTEoC9gwFev3s3AOhgqhj0"
              "rmeexlilxAn1WCACskVBdimLZyQhIJpGppJjosIoYw0rCBMSJBLMDSO+cvosVje2sDQ3i5wzUkq4"
              "ZGEOr/2+Xbjz+EnsWZpHj1aHg2Z0BM8pJsUVbnolrUisUrsyt9bZmZNaGZwZHAhBGGfHE/zHPZdi"
              "z9xc8RYCIjG2+h4PvfA85ruIJNo+6ZjcRju+G9KuXqBUsQjK2nO1J9FIQ2yzRg0pBMwMAk6ub+Kr"
              "33sOP37Ny21HCCLAb958A+ZnBxgJObvnwlvnZ8To7qoYmXq0LCbVeb3Dd+g8XhE/BB0Bv3DVK3Rb"
              "sYh1AgFff+EcvjceYcfM0OeFJvUhzZ0AQDlXvqtpo6PnHm5U6FMHKYqmK/lOoYDBe048VQyksQ4I"
              "rtq9A3/w9pvw/+xLCf1eq919/3oGvRpL9CLWNfXivaYQJ2VQpaFhojCtcghL5DtUioxR9YU2GrJm"
              "NkEwOzvAA6eew2gyQRej6xhzVn0iXYxhR6N3fAkG/iXJWbpQYL5dJUxlBmOLxilnPPD8OQxj5yMs"
              "o0PcUIrWoViKpLQlwoyc+1XORKeoixCmjEZ1jpBBCvx8MTfAWbhhR3ji/Dr+9+kzYCJMckafpZJi"
              "qnUUXfbNvtNRl319yU6kIVSl+X95ZH+uesCFf1cokV4yJmUagWOrL+CbmxtYiFHlOXU9wVfR60G5"
              "oQOQQ9dBAp+KiPwIYrw2973UuxigSvylIthSebhUGgCh6/Db/+ufcOeuZexZnMf/T1/nxyP83hOP"
              "g2IHJvaKRrq3ZuUeDfgk0X4OWajrhMZbj0RI/pxQfieputEXYUmmjDVFPOlVWBhEfOPcOt721/fg"
              "zVdfhl1zQ0ejjHZaQL7Wie2bN+2CG9qluDrpdfTri3TivysHnX09lESwOpng3nNn8J3xBJcMBs0w"
              "ET7SkWa72nK/yYQhQsiZKONzdODQJ3YnnjlOXbcrp0lZyQzwxbO65miklJZhzUnMhFFOWB9PVIFR"
              "+Rvb5LMFlmx7XPZ8I/L2rcXQTCG4dupii7ftPgZV0bmrzlDGyPODAeZYNUt6UZNpvlVVRqbw57py"
              "yiLCMSL1/dkUJtcSAFz9O0f+MC7ufE/aerEHSSRV2ws3s+5g6nNThgbdm6i8UMtOut7YtD2hzsrJ"
              "dlBsDMOMAIURIXgiddCmFYipvRMDvAM3DJc0FwWxlga6NSlTUzaWVoOkx1RlAn3csRy3zj3/R1+7"
              "8Q2/yIBQHM58QEYboxAiMbEUSRv5LqhJ/mE6QG52PnVFO0GQqXAqCSiEOQqw7AEkmcYXKZffZSrV"
              "o2cl2SWXpK4sY4Kgl1z4HBJfx8woiDrrZ/WSVMxafpeNxFZcJK3BdTRrO2O2MsEQCTFSv74xCjT4"
              "AESIV44c5cd//aeelDx+f5ifD8ToJTYSkGj7ZOJDQpjC1I1koVH1g4WC5TqVNQmd9mKkdIiEQtW6"
              "gl9yzV/Nohvx9KIuN7fRsa1HDzUTddpdZGDdu9RbaISqaLMcJEAflhdDGk/e/9Uf+ZEnV2CZYuUI"
              "X3cdwng+PhAW5g+m0XoP5gjt3l11ajgixrrB1yjDSHclKgJnnyaYco2Jp/Yn6j2RxAkxahBtWW4j"
              "B7gmO2YDfmTrOFR0ioR6hxkCpu+doccB7Ryk2WiU1HdLy3Gytv6Pa8Olm1597Fg6urKSS1Ny3aNy"
              "7PCt457yiky2TvPsXARJz6G5rUwIPuSfrmzs3E9icXFnSegZwsnVXNTcK8Nn4kaPUzVMpjodcWPU"
              "VVrlxJs7OKhCg5vk7yq5ph+U0msXqjzZnSUEJNJ38wux39w6PRlvrhz7/u8fH3300XJfl3p7nHJL"
              "mAO/94lrsLx0Nw9n9+ettQkYEYF1i14KrarLbKQKVyfNURO21Ml/6Wl0WlkYy0qB2pUmnYaYR/g2"
              "M00LD6RV4KK5PQ/qENDxm75XpLorEhqjEkSCoO+WlrvJaHRqtL52y9def/Njh0T4MLW3xwGAw5Rx"
              "5Eh4/Lfe+Rg2n39DHo8e4qXlTgKTAL2QSOY6ORUOqiwVNxYHbrTSNmYJ2rmzj7HLwA4+Ct6uN3SJ"
              "nqn4lRATpgua2rKpfWHDYpvQoHobDVYOmyBCgj5yoHjJrm48Gj3Unz/3hq+9/ubHVo4cCWYc2HKR"
              "fx09Klg5Es7+wc+e2/vaV96ZZvcSRT4YFhZmcnH8TMyZqjKVrNMnrmpVmwywk+rs9/sxiQ2an8ny"
              "k3pkXWFvyDFIHU9ppAYqiy0mDGe7WQrVvMZ6iy4mFiIpQ5au48GOZU5ZNvut0X9f/btPv+vr//n2"
              "sytHjoSjt/57t+i6yE3erv6TT72SZ/iXwFjhQbcPMUByRu4nEEkuiGwX7ojrbXBMuGS3mmAXnecp"
              "Ej00DOb2pWFplvCE6+16SGTqtjoMNPetEWUNS8WMXYcQO0jKyJPxMznyUaT0oYdvuPH/8iZv7W0C"
              "D90X7j98c7lN4If/fm83GLyJKd8yG+j6YceXR6KlpLf1cHWsVxldY7CsqG1AMAMpIq/Ujw75nE1o"
              "hnhsGKbe/I2avVm/j4fSplnrWiBCRyw9sLolcjITHskU7s7j1c8/fOOPl9sE3ndfvP9Hb07bl6zt"
              "698Ay9spmDMPqOEAAAAASUVORK5CYII=")

UA_DESKTOP = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

URL_PATTERN = re.compile(r'https?://[^\s<>"\'）)】\]，,;；]+', re.I)

SUPPORT = ("抖音 · 快手 · 小红书 · 微博 · B站 · 西瓜视频 · AcFun · 知乎 · 网易云 · "
           "YouTube · TikTok · Instagram · Twitter/X · Facebook · Vimeo · SoundCloud")


def res_path(name):
    """打包后取 exe 内捆绑资源，源码运行取脚本同目录"""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def _warm_imports():
    """把 yt-dlp 和它要用的标准库先在启动时导进内存。
    打包成单文件后，程序跑在系统临时目录里；如果中途被系统/清理软件删掉，
    再 import 就会报 base_library.zip 找不到。提前导入可以避免这个问题。"""
    mods = ["yt_dlp", "yt_dlp.YoutubeDL", "yt_dlp.utils", "http.cookiejar", "urllib.request",
            "email.parser", "mimetypes", "hashlib", "hmac", "struct", "json", "re",
            "subprocess", "socket", "ssl", "sqlite3", "tempfile", "shutil", "random",
            "mutagen", "brotli", "certifi", "requests", "websockets", "xml.etree.ElementTree",
            "unicodedata", "base64", "binascii", "codecs", "collections", "datetime"]
    for m in mods:
        try:
            __import__(m)
        except Exception as e:
            dlog("预热 %s 失败: %s" % (m, e))


def cleanup_leftovers():
    """启动时清理上一次更新留下的旧程序与旧安装包"""
    cur = os.path.abspath(sys.executable) if getattr(sys, "frozen", False) else None
    if not cur:
        return
    side = os.path.dirname(cur)
    for p in (cur + ".old", cur + ".bak",
              os.path.join(side, "_更新包.exe"),
              os.path.join(side, "_更新包.exe.part"),
              os.path.join(side, "VideoDownloader_new.exe"),
              os.path.join(side, "vd_log.txt")):  # 旧版遗留的日志，别再留在桌面

        try:
            if os.path.exists(p):
                os.remove(p)
        except Exception:
            pass
    updir = os.path.join(app_dir(), "update_download")
    try:
        if os.path.isdir(updir):
            import shutil
            shutil.rmtree(updir, ignore_errors=True)
    except Exception:
        pass


def app_dir():
    """打包成 exe 后取 exe 所在目录，源码运行取脚本目录"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def config_path():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "VDDesktop")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "config.json")


def load_config():
    try:
        with open(config_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg):
    try:
        with open(config_path(), "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def default_dir():
    home = os.path.expanduser("~")
    for name in ("Downloads", "下载"):
        p = os.path.join(home, name)
        if os.path.isdir(p):
            return p
    return home


def find_ffmpeg():
    """优先用程序目录里的 ffmpeg.exe，其次看 PATH"""
    local = os.path.join(app_dir(), "ffmpeg.exe")
    if os.path.isfile(local):
        return local
    from shutil import which
    return which("ffmpeg")


def extract_urls(text):
    urls, seen = [], set()
    for m in URL_PATTERN.finditer(text or ""):
        u = m.group(0).rstrip("。.,;:!?、")
        if u not in seen:
            seen.add(u)
            urls.append(u)
    return urls


def human_time(sec):
    try:
        sec = int(sec or 0)
    except Exception:
        return ""
    return "%d:%02d" % (sec // 60, sec % 60)


class Task(object):
    def __init__(self, idx, url):
        self.idx = idx
        self.url = url
        self.title = ""
        self.duration = ""
        self.status = "等待"
        self.progress = 0.0


def detect_default_browser():
    """猜测系统默认浏览器，返回 Chrome / Edge / Firefox / None"""
    if sys.platform != "win32":
        return None
    try:
        import winreg
        key = (r"Software\Microsoft\Windows\Shell\Associations"
               r"\UrlAssociations\https\UserChoice")
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            prog = winreg.QueryValueEx(k, "ProgId")[0].lower()
        if "chrome" in prog:
            return "Chrome"
        if "edge" in prog or "msedge" in prog:
            return "Edge"
        if "firefox" in prog:
            return "Firefox"
    except Exception:
        pass
    return None


def find_browser_exe(browser):
    """找 Edge / Chrome 的 exe 路径（注册表 + PATH + 常见安装位置，兼容非 C 盘）"""
    if sys.platform != "win32":
        return None
    names = ["msedge.exe"] if browser == "Edge" else ["chrome.exe"]
    # 1) 注册表 App Paths（最可靠，系统装在哪个盘都能找到）
    try:
        import winreg
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for nm in names:
                try:
                    k = winreg.OpenKey(hive, "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\" + nm)
                    path, _ = winreg.QueryValueEx(k, "")
                    winreg.CloseKey(k)
                    path = path.strip().strip('"')
                    if os.path.isfile(path):
                        return path
                except Exception:
                    pass
    except Exception:
        pass
    # 2) PATH
    for nm in names:
        w = shutil.which(nm)
        if w and os.path.isfile(w):
            return w
    # 3) 常见安装位置（盘符从环境变量取，不写死 C:）
    roots = []
    for env in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "LOCALAPPDATA"):
        v = os.environ.get(env)
        if v:
            roots.append(v)
    for r in roots:
        for nm in names:
            for sub in ("Microsoft\\Edge\\Application", "Google\\Chrome\\Application"):
                c = os.path.join(r, sub, nm)
                if os.path.isfile(c):
                    return c
    return None


def login_profile_dir(browser):
    """登录配置放用户数据目录，不放在程序旁边（避免被更新/清理误删）"""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "VDDesktop", "login_" + browser.lower())
    legacy = os.path.join(app_dir(), "login_" + browser.lower())
    try:
        if os.path.isdir(legacy) and not os.path.isdir(d):
            import shutil
            os.makedirs(os.path.dirname(d), exist_ok=True)
            shutil.move(legacy, d)
            dlog("已把登录配置迁移到 " + d)
    except Exception as e:
        dlog("迁移登录配置失败: %s" % e)
    return d


class App(object):
    def __init__(self):
        self._silent_check = True
        self.cfg = load_config()
        self.outdir = self.cfg.get("outdir") or default_dir()
        self.quality = self.cfg.get("quality") or "最高画质"
        self.cookie_browser = self.cfg.get("cookie_browser") or "不使用"
        self.cookie_profile = self.cfg.get("cookie_profile") or ""
        self.dy_direct = {}
        self._dy_info_cache = {}
        self._login_proc = None
        self.cookiefile = self.cfg.get("cookiefile") or ""
        if not self.cookiefile:
            ctxt = os.path.join(_log_dir(), "cookies.txt")
            if os.path.isfile(ctxt) and os.path.getsize(ctxt) > 200:
                self.cookiefile = ctxt
        self.tasks = []
        self.jobs = queue.Queue()
        self.events = queue.Queue()
        self.busy = False
        self.cancelled = False

        self.root = tk.Tk()
        try:
            self._icon_img = tk.PhotoImage(data=ICON_B64)
            self.root.iconphoto(True, self._icon_img)
        except Exception:
            pass
        try:
            ico = res_path("app.ico")
            if os.path.isfile(ico):
                self.root.iconbitmap(ico)
        except Exception:
            pass
        try:
            png = res_path("app.png")
            if os.path.isfile(png):
                self._icon_img = tk.PhotoImage(file=png)
                self.root.iconphoto(True, self._icon_img)
        except Exception:
            pass
        self.root.title("%s · 电脑版 %s" % (APP_NAME, APP_VER))
        self.root.geometry("980x760")
        self.root.minsize(820, 620)
        self.root.configure(bg=BG)
        self._style()
        self._build()
        self._pump()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---------- 样式 ----------
    def _style(self):
        s = ttk.Style(self.root)
        try:
            s.theme_use("clam")
        except Exception:
            pass
        s.configure("Treeview", background=CARD, fieldbackground=CARD, foreground=FG,
                    rowheight=28, borderwidth=0, font=("Microsoft YaHei UI", 9))
        try:
            s.configure("Horizontal.TProgressbar", troughcolor="#21262d", background=BLUE,
                        bordercolor="#21262d", lightcolor=BLUE, darkcolor=BLUE)
        except Exception:
            pass
        s.configure("Treeview.Heading", background="#21262d", foreground=SUB,
                    borderwidth=0, font=("Microsoft YaHei UI", 9))
        s.map("Treeview", background=[("selected", "#1f6feb")])
        s.configure("green.Horizontal.TProgressbar", troughcolor="#21262d",
                    background=GREEN, borderwidth=0)
        s.configure("TCombobox", fieldbackground=CARD, background=CARD, foreground=FG)
        # 下拉框在 Windows 上默认是白底，单独做一个深色样式
        try:
            s.configure("Dark.TCombobox", fieldbackground=CARD, background=CARD,
                        foreground=FG, selectbackground=CARD, selectforeground=FG,
                        arrowcolor=SUB, bordercolor="#30363d", lightcolor=CARD,
                        darkcolor=CARD, insertcolor=FG, padding=(6, 3))
            s.map("Dark.TCombobox",
                  fieldbackground=[("readonly", CARD), ("!disabled", CARD)],
                  background=[("readonly", CARD), ("active", "#21262d"),
                              ("pressed", "#21262d"), ("!disabled", CARD)],
                  foreground=[("readonly", FG), ("disabled", SUB)],
                  selectbackground=[("readonly", CARD)],
                  selectforeground=[("readonly", FG)],
                  arrowcolor=[("readonly", SUB), ("active", FG)])
        except Exception:
            pass
        # 展开后的下拉列表也要深色
        for pat, val in (("*TCombobox*Listbox.background", CARD),
                         ("*TCombobox*Listbox.foreground", FG),
                         ("*TCombobox*Listbox.selectBackground", BLUE),
                         ("*TCombobox*Listbox.selectForeground", "#ffffff")):
            try:
                self.root.option_add(pat, val, "80")
            except Exception:
                pass
        # 滚动条跟着深色背景走，别再用默认的白色
        for _n in ("Vertical.TScrollbar", "Horizontal.TScrollbar"):
            try:
                s.configure(_n, gripcount=0, background="#2b333d", darkcolor="#2b333d",
                            lightcolor="#2b333d", troughcolor=CARD, bordercolor=CARD,
                            arrowcolor="#8b949e", arrowsize=12, width=10, relief="flat")
                s.map(_n, background=[("active", "#3c4650"), ("disabled", CARD)],
                      arrowcolor=[("active", FG)])
            except Exception:
                pass

    def _build(self):
        # 顶部标题行
        head = tk.Frame(self.root, bg=BG)
        head.pack(fill="x", padx=18, pady=(16, 6))
        tk.Label(head, text=APP_NAME, bg=BG, fg=FG,
                 font=("Microsoft YaHei UI", 20, "bold")).pack(side="left")
        tk.Label(head, text="  电脑版 · 本地运行 · 可自定义保存目录", bg=BG, fg=SUB,
                 font=("Microsoft YaHei UI", 10)).pack(side="left", pady=(8, 0))
        self.notes_btn = tk.Button(head, text="更新日志", command=self.show_notes,
                                   bg=CARD, fg=FG, bd=0, font=("Microsoft YaHei UI", 9),
                                   width=9, height=1, cursor="hand2",
                                   activebackground="#21262d", activeforeground=FG)
        self.notes_btn.pack(side="right", padx=(6, 0), pady=(6, 0))
        self.upd_btn = tk.Button(head, text="检查更新", command=self.check_update_ui,
                                 bg=CARD, fg=FG, bd=0, font=("Microsoft YaHei UI", 9),
                                 width=9, height=1, cursor="hand2",
                                 activebackground="#21262d", activeforeground=FG)
        self.upd_btn.pack(side="right", padx=(8, 0), pady=(6, 0))
        self.ver_lbl = tk.Label(head, text="v" + APP_VER, bg=BG, fg=SUB,
                                font=("Microsoft YaHei UI", 9))
        self.ver_lbl.pack(side="right", pady=(8, 0))

        # 保存目录行
        row = tk.Frame(self.root, bg=BG)
        row.pack(fill="x", padx=18, pady=(8, 4))
        tk.Label(row, text="保存到", bg=BG, fg=FG,
                 font=("Microsoft YaHei UI", 10)).pack(side="left")
        self.dir_var = tk.StringVar(value=self.outdir)
        entry = tk.Entry(row, textvariable=self.dir_var, bg=CARD, fg=FG, bd=0,
                         insertbackground=FG, font=("Microsoft YaHei UI", 9))
        entry.pack(side="left", fill="x", expand=True, padx=(10, 8), ipady=7)
        self.btn(row, "选择文件夹", self.choose_dir, primary=False, width=11)
        self.btn(row, "打开目录", self.open_dir, primary=False, width=9)

        # 输入区
        tk.Label(self.root, text="粘贴链接（可带分享文案，会自动只取链接；多条用换行分隔）",
                 bg=BG, fg=SUB, font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=18, pady=(10, 4))

        box = tk.Frame(self.root, bg=CARD)
        box.pack(fill="x", padx=18, pady=(0, 8))
        self.input = tk.Text(box, height=5, bg=CARD, fg=FG, bd=0, wrap="char",
                             insertbackground=FG, font=("Microsoft YaHei UI", 10),
                             padx=10, pady=8)
        self.input.pack(fill="x")
        self.input.bind("<Control-Return>", lambda e: self.start())

        # 按钮行
        bar = tk.Frame(self.root, bg=BG)
        bar.pack(fill="x", padx=18, pady=(0, 8))
        self.btn(bar, "识别链接", self.recognize, primary=True, width=12)
        self.btn(bar, "开始下载", self.start, primary=True, width=12)
        self.btn(bar, "清空", self.clear, primary=False, width=8)
        tk.Label(bar, text="画质", bg=BG, fg=FG, font=("Microsoft YaHei UI", 10)).pack(side="left", padx=(16, 6))
        self.q_cb = ttk.Combobox(bar, values=["最高画质", "1080p", "720p"], width=9, state="readonly",
                                 style="Dark.TCombobox")
        self.q_cb.set(self.quality)
        self.q_cb.pack(side="left")

        tk.Label(bar, text="Cookie", bg=BG, fg=FG,
                 font=("Microsoft YaHei UI", 10)).pack(side="left", padx=(16, 6))
        self.ck_cb = ttk.Combobox(bar, values=["不使用", "Chrome", "Edge", "Firefox"],
                                  style="Dark.TCombobox",
                                  width=9, state="readonly")
        self.ck_cb.set(self.cookie_browser)
        self.ck_cb.pack(side="left")
        self.ck_cb.bind("<<ComboboxSelected>>", lambda e: self.on_cookie_change())
        self.btn(bar, "扫码登录", self.douyin_login, primary=True, width=9)
        self.btn(bar, "cookies.txt", self.choose_cookies_file, primary=False, width=10)

        self.ck_hint = tk.Label(self.root, text=self.cookie_hint_text(), bg=BG, fg=SUB,
                                font=("Microsoft YaHei UI", 8), anchor="w")
        self.ck_hint.pack(fill="x", padx=18, pady=(0, 4))

        # 任务表
        mid = tk.Frame(self.root, bg=BG)
        mid.pack(fill="both", expand=True, padx=18, pady=(4, 6))
        cols = ("no", "title", "author", "dur", "status", "bar")
        self.tree = ttk.Treeview(mid, columns=cols, show="headings", height=8)
        for c, t, w, a in (("no", "序号", 45, "center"), ("title", "标题", 330, "w"),
                           ("author", "作者", 110, "w"), ("dur", "时长", 65, "center"),
                           ("status", "状态", 150, "w"), ("bar", "进度", 105, "w")):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor=a)
        sb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        # 总体进度条（当前下载）
        pf = tk.Frame(self.root, bg=BG)
        pf.pack(fill="x", padx=18, pady=(2, 4))
        self.pbar = ttk.Progressbar(pf, mode="determinate", maximum=100, length=300)
        self.pbar.pack(side="left", fill="x", expand=True)
        self.plbl = tk.Label(pf, text="0%", bg=BG, fg=SUB, width=6,
                             font=("Consolas", 9), anchor="e")
        self.plbl.pack(side="left", padx=(8, 0))

        # 日志
        tk.Label(self.root, text="运行日志", bg=BG, fg=SUB,
                 font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=18)
        logf = tk.Frame(self.root, bg=CARD)
        logf.pack(fill="x", padx=18, pady=(4, 8))
        self.log = tk.Text(logf, height=7, bg=CARD, fg=SUB, bd=0, wrap="word",
                           font=("Consolas", 9), padx=10, pady=8)
        lsb = ttk.Scrollbar(logf, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=lsb.set)
        self.log.pack(side="left", fill="both", expand=True)
        lsb.pack(side="right", fill="y")

        foot = tk.Label(self.root, text=SUPPORT + "   |   " + AUTHOR, bg=BG, fg="#6e7681",
                        font=("Microsoft YaHei UI", 8), wraplength=920, justify="center")
        foot.pack(pady=(0, 12))

        self.logline("就绪 · ffmpeg: " + (find_ffmpeg() or "未找到（部分站点需要它来合并音视频）"))
        self._cleanup_old()
        if getattr(sys, "frozen", False):
            self.root.after(1500, lambda: self.check_update(silent=True))

    def btn(self, parent, text, cmd, primary=True, width=10):
        bg = BLUE if primary else CARD
        fg = "#ffffff" if primary else FG
        b = tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg, bd=0,
                      font=("Microsoft YaHei UI", 9), width=width, height=2,
                      activebackground="#1f6feb" if primary else "#21262d",
                      activeforeground=fg, cursor="hand2")
        b.pack(side="left", padx=(0, 8))
        return b

    # ---------- 基础动作 ----------
    def logline(self, text):
        self.log.insert("end", text + "\n")
        self.log.see("end")

    def set_status(self, idx, status, progress=None):
        self.events.put(("row", idx, status, progress))

    def cookie_hint_text(self):
        if self.cookiefile:
            return "Cookie 文件: " + self.cookiefile
        try:
            if len(load_cookies()) > 10:
                return "已登录（Cookie 已保存在本机，重启/更新都不用再登录）"
        except Exception:
            pass
        if self.cookie_profile and os.path.isdir(self.cookie_profile):
            return "已登录（%s），Cookie 会自动读取" % self.cookie_browser
        if self.cookie_browser != "不使用":
            return "点「扫码登录」用 %s 登录一次抖音，之后自动读取 Cookie" % self.cookie_browser
        return "抖音、小红书、Instagram 等站点现在需要 Cookie，选一个浏览器或 cookies.txt"

    def on_cookie_change(self):
        self.cookie_browser = self.ck_cb.get()
        self.cfg["cookie_browser"] = self.cookie_browser
        if self.cookie_browser != "不使用":
            self.cookiefile = ""
            self.cfg["cookiefile"] = ""
        save_config(self.cfg)
        self.ck_hint.config(text=self.cookie_hint_text())
        self.logline("Cookie 来源: " + self.cookie_browser)

    def choose_cookies_file(self):
        p = filedialog.askopenfilename(title="选择 cookies.txt",
                                       filetypes=[("Cookie 文件", "*.txt"), ("所有文件", "*.*")])
        if p:
            self.cookiefile = p
            self.cfg["cookiefile"] = p
            save_config(self.cfg)
            self.ck_hint.config(text=self.cookie_hint_text())
            self.logline("Cookie 文件: " + p)

    def douyin_login(self):
        """用独立临时配置的浏览器打开抖音登录，登录完关掉它再读 Cookie"""
        browser = self.cookie_browser if self.cookie_browser != "不使用" else (detect_default_browser() or "Edge")
        exe = find_browser_exe(browser)
        if exe is None:
            browser = "Chrome" if browser == "Edge" else "Edge"
            exe = find_browser_exe(browser)
        self.cookie_browser = browser
        self.ck_cb.set(browser)
        self.cfg["cookie_browser"] = browser
        save_config(self.cfg)

        if exe is None:
            # 找不到 Edge/Chrome，退回系统默认浏览器（需用户自己关
            import webbrowser
            webbrowser.open("https://www.douyin.com/")
            self.logline("没找到 Edge/Chrome，已用系统浏览器打开 douyin.com")
            messagebox.showinfo("登录抖音", "请在浏览器里扫码登录抖音，登录后完全关闭浏览器，\n"
                                          "再点「cookies.txt」选择导出文件，或把 Cookie 选成该浏览器。")
            return

        prof = login_profile_dir(browser)
        ok, used_prof, code = self._spawn_login_browser(exe, prof, browser)
        if not ok:
            # 最后兜底：用系统默认浏览器打开（可能复用你正在用的浏览器）
            try:
                import webbrowser
                webbrowser.open("https://www.douyin.com/")
                self.logline("独立窗口启动失败（退出码 %s），已改用系统默认浏览器打开抖音" % code)
                messagebox.showinfo("登录抖音",
                                    "独立登录窗口没起来（这次是浏览器自身拒绝了启动）。\n"
                                    "我已经用你默认的浏览器打开抖音，登录后请：\n"
                                    "1. 完全关闭那个浏览器\n"
                                    "2. 把上面的「Cookie 来源」选成你刚才用的浏览器\n"
                                    "3. 再点「开始下载」")
                return
            except Exception as e:
                self.logline("连默认浏览器也打不开: %s" % e)
                messagebox.showwarning("登录抖音", "打不开浏览器，请手动打开浏览器登录抖音后再点「开始下载」")
                return
        self.logline("已用独立的 %s 打开抖音登录页（配置: %s，不影响你平时的浏览器）" % (browser, used_prof))

        ans = messagebox.askokcancel(
            "扫码登录抖音",
            "1. 在弹出的窗口里扫码或登录抖音（只需登录一次）\n"
            "2. 看到推荐视频就说明登录成功\n"
            "3. 点「确定」后我会自动关掉这个窗口并读取 Cookie\n\n"
            "这个窗口用的是独立配置，不会动你平时浏览器的账号和数据。")
        if not ans:
            self.logline("已取消，登录后仍可再点「扫码登录」")
            self._kill_login()
            return
        # 先把登录后的 Cookie 存下来（更新/重启都不用再登）
        n_ck = 0
        try:
            if getattr(self, "_login_port", 0):
                n_ck = save_cookies_from_cdp(self._login_port)
        except Exception as e:
            dlog("保存 Cookie 异常: %s" % e)
        self._close_login_browser()
        dflt = os.path.join(used_prof, "Default")
        self.cookie_profile = dflt if os.path.isdir(dflt) else used_prof
        self.cfg["cookie_profile"] = self.cookie_profile
        self.cfg["cookiefile"] = ""
        self.cookiefile = ""
        save_config(self.cfg)
        self.ck_hint.config(text=self.cookie_hint_text())
        cks = load_cookies()
        if cks:
            txt = os.path.join(_log_dir(), "cookies.txt")
            if write_netscape_cookies(cks, txt):
                self.cookiefile = txt
                self.cfg["cookiefile"] = txt
                save_config(self.cfg)
                self.ck_hint.config(text=self.cookie_hint_text())
            self.logline("登录完成，已保存 %d 条 Cookie（存本机，更新或重启都不用再登录）" % len(cks))
        else:
            self.logline("登录窗口已关闭，但没取到 Cookie；识别失败的话请再点一次「扫码登录」")

    def _spawn_login_browser(self, exe, prof, browser):
        """启动唯一的登录窗口（独立配置 + 调试端口，方便优雅关闭让 Cookie 落盘）
        返回 (成功, 实际目录, 退出码)"""
        code = None
        try:
            os.makedirs(prof, exist_ok=True)
        except Exception:
            pass
        # 先把占着这个配置的旧窗口全部关掉，保证只有一个
        try:
            if self._login_proc is not None and self._login_proc.poll() is None:
                self._close_login_browser()
        except Exception:
            pass
        _kill_browser_on_profile(prof)
        for attempt in range(3):
            if attempt == 0:
                _clear_profile_locks(prof)
            elif attempt == 1:
                _clear_profile_locks(prof)
            else:
                # 目录可能是坏的，整个挪走用干净目录（不新建第二个配置）
                try:
                    bak = prof + ".bad"
                    import shutil
                    if os.path.isdir(bak):
                        shutil.rmtree(bak, ignore_errors=True)
                    if os.path.isdir(prof):
                        shutil.move(prof, bak)
                except Exception:
                    pass
                try:
                    os.makedirs(prof, exist_ok=True)
                except Exception:
                    pass
            port = _free_port()
            try:
                args = [exe, "--remote-debugging-port=%d" % port,
                        "--user-data-dir=" + prof, "--no-first-run",
                        "--no-default-browser-check", "--new-window",
                        "https://www.douyin.com/"]
                proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception as e:
                dlog("启动浏览器失败(%s): %s" % (prof, e))
                code = -1
                continue
            self._login_proc = proc
            self._login_port = port
            alive = True
            for _ in range(16):  # 等 4 秒看它是不是自己退了
                time.sleep(0.25)
                if proc.poll() is not None:
                    alive = False
                    break
            if alive:
                return True, prof, None
            code = proc.returncode
            dlog("浏览器启动后立即退出(退出码 %s, 配置 %s，第%d次)" % (code, prof, attempt + 1))
            try:
                proc.wait(timeout=3)
            except Exception:
                pass
            self._login_proc = None
            _kill_browser_on_profile(prof)
        return False, prof, code

    def _close_login_browser(self):
        """优雅关掉登录窗口：先 CDP Browser.close，再兜底 taskkill（不加 /F 先试）"""
        proc = self._login_proc
        port = getattr(self, "_login_port", 0)
        if proc is not None and proc.poll() is None and port:
            try:
                import urllib.request
                with urllib.request.urlopen("http://127.0.0.1:%d/json/version" % port, timeout=3) as r:
                    ws = json.loads(r.read().decode("utf-8", "replace")).get("webSocketDebuggerUrl")
                if ws:
                    sock = cdp_ws_connect(ws)
                    try:
                        _ws_send(sock, {"id": 99, "method": "Browser.close"})
                        for _ in range(20):
                            time.sleep(0.2)
                            if proc.poll() is not None:
                                break
                    finally:
                        try:
                            sock.close()
                        except Exception:
                            pass
                    dlog("已用 CDP 优雅关闭登录窗口")
            except Exception as e:
                dlog("CDP 关闭失败: %s" % e)
            try:
                if proc.poll() is None:
                    time.sleep(1.0)
            except Exception:
                pass
        self._kill_login(soft=True)

    def _kill_login(self, soft=False):
        p = self._login_proc
        self._login_proc = None
        if p is None or p.poll() is not None:
            return
        try:
            if soft and sys.platform == "win32":
                # 先温和关闭，让浏览器把 Cookie 写进磁盘
                subprocess.run(["taskkill", "/T", "/PID", str(p.pid)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                for _ in range(25):
                    if p.poll() is not None:
                        return
                    time.sleep(0.2)
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                p.terminate()
        except Exception:
            try:
                p.terminate()
            except Exception:
                return
        try:
            p.wait(timeout=8)
        except Exception:
            pass

    def show_notes(self):
        messagebox.showinfo("更新日志", "版本 %s 更新内容\n\n%s" % (APP_VER, THIS_VERSION_NOTES))

    # ---------- 自动更新 ----------
    def _cleanup_old(self):
        try:
            old = (sys.executable if getattr(sys, "frozen", False) else "") + ".old"
            if old and os.path.exists(old):
                os.remove(old)
        except Exception:
            pass

    def check_update_ui(self):
        self.check_update(silent=False)

    def check_update(self, silent=True):
        self._silent_check = silent

        def work():
            try:
                import urllib.request
                url = UPDATE_MANIFEST + "?t=%d" % int(time.time() * 1000)
                req = urllib.request.Request(url, headers={"User-Agent": "VDDesktop/%s" % APP_VER})
                with urllib.request.urlopen(req, timeout=15) as r:
                    info = json.loads(r.read().decode("utf-8", "ignore"))
                vc = int(info.get("versionCode") or 0)
                name = info.get("versionName") or ""
                changes = info.get("changes") or info.get("note") or ""
                urls = []
                for k in ("urlCdn", "url", "urlAlt"):
                    u = info.get(k) or ""
                    if u.startswith("http") and u not in urls:
                        urls.append(u)
                if not urls:
                    urls = [UPDATE_EXE]
                self._upd_urls = urls
                if vc > APP_VC:
                    self.events.put(("update", vc, name, changes))
                else:
                    self.events.put(("uptodate", name or APP_VER))
            except Exception as e:
                self.events.put(("upfail",))
        threading.Thread(target=work, daemon=True).start()

    def prompt_update(self, vc, name, changes):
        """自绘更新窗口：立即更新 / 稍后"""
        self._new_ver = name or ""
        top = tk.Toplevel(self.root)
        top.title("发现新版本 %s" % name)
        top.configure(bg=BG)
        top.resizable(False, False)
        try:
            top.transient(self.root)
        except Exception:
            pass
        tk.Label(top, text="发现新版本 " + name, bg=BG, fg=FG,
                 font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w", padx=18, pady=(16, 6))
        body = tk.Label(top, text="本次更新内容：\n" + (changes.strip() or "新版本 %s 已发布" % name),
                        bg=BG, fg=SUB, justify="left", wraplength=520,
                        font=("Microsoft YaHei UI", 9))
        body.pack(anchor="w", padx=18, pady=(0, 10))
        tk.Label(top, text="点「立即更新」会自动下载新版本、替换当前程序并重新打开。",
                 bg=BG, fg=SUB, justify="left", wraplength=520,
                 font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=18, pady=(0, 14))

        def go():
            top.destroy()
            self.download_update()

        row = tk.Frame(top, bg=BG)
        row.pack(fill="x", padx=18, pady=(0, 16))
        b1 = tk.Button(row, text="立即更新", command=go, bg=BLUE, fg="#ffffff", bd=0,
                       font=("Microsoft YaHei UI", 10), width=12, height=2,
                       activebackground="#1f6feb", activeforeground="#ffffff", cursor="hand2")
        b1.pack(side="right", padx=(8, 0))
        b2 = tk.Button(row, text="稍后", command=top.destroy, bg=CARD, fg=FG, bd=0,
                       font=("Microsoft YaHei UI", 10), width=10, height=2,
                       activebackground="#21262d", activeforeground=FG, cursor="hand2")
        b2.pack(side="right")
        top.update_idletasks()
        try:
            x = self.root.winfo_rootx() + (self.root.winfo_width() - top.winfo_width()) // 2
            y = self.root.winfo_rooty() + 120
            top.geometry("+%d+%d" % (max(x, 0), max(y, 0)))
        except Exception:
            pass
        top.grab_set()

    def show_upd_progress(self):
        """更新下载进度窗口：进度条 + 百分比"""
        stale = getattr(self, "_updwin", None)
        alive = False
        try:
            alive = stale is not None and bool(stale.winfo_exists())
        except Exception:
            alive = False
        if alive:
            try:
                self._updbar["value"] = 0
                self._updlbl.config(text="0%  正在连接…")
                stale.lift()
            except Exception:
                pass
            return
        self._updwin = None
        self._updbar = None
        self._updlbl = None
        try:
            top = tk.Toplevel(self.root)
            self._updwin = top
            top.title("正在下载新版本 %s" % (getattr(self, "_new_ver", "") or ""))

            top.configure(bg=BG)
            top.resizable(False, False)
            top.transient(self.root)
            tk.Label(top, text="正在下载新版本 %s……" % (getattr(self, "_new_ver", "") or APP_VER),
                     bg=BG, fg=FG,
                     font=("Microsoft YaHei UI", 11)).pack(pady=(18, 8))
            self._updbar = ttk.Progressbar(top, mode="determinate", maximum=100)
            self._updbar.pack(fill="x", padx=26)
            self._updlbl = tk.Label(top, text="0%  正在连接…", bg=BG, fg=SUB, font=("Consolas", 10))
            self._updlbl.pack(pady=(6, 0))
            top.protocol("WM_DELETE_WINDOW", lambda: None)
            top.update_idletasks()
            w, h = 420, 150
            x = self.root.winfo_rootx() + (max(self.root.winfo_width(), w) - w) // 2
            y = self.root.winfo_rooty() + (max(self.root.winfo_height(), h) - h) // 2
            top.geometry("%dx%d+%d+%d" % (w, h, max(x, 0), max(y, 0)))
            top.lift()
        except Exception:
            self._updwin = None

    def close_upd_progress(self):
        try:
            if getattr(self, "_updwin", None) is not None:
                self._updwin.destroy()
        except Exception:
            pass
        self._updwin = None

    def download_update(self):
        self.show_upd_progress()
        urls = getattr(self, "_upd_urls", None) or [UPDATE_EXE]

        def work():
            target_dir = os.path.dirname(sys.executable)
            tmp = os.path.join(target_dir, "_更新包.exe")
            part = tmp + ".part"
            self.events.put(("updprog", 0, "正在连接…"))
            ok = False
            for attempt in range(4):
                url = urls[attempt % len(urls)] if urls else UPDATE_EXE
                tag = "CDN" if "doubaocdn" in url else "GitHub"

                def emit(got, total, sp, _tag=tag):
                    eta = ""
                    if total and sp > 0.01:
                        left = max(0, int((total - got) / 1048576.0 / sp))
                        eta = " · 剩 %d:%02d" % (left // 60, left % 60)
                    if total:
                        self.events.put(("updprog", min(99, int(got * 100.0 / total)),
                                         "%s · %.1f / %.1f MB · %.2f MB/s%s" % (
                                             _tag, got / 1048576.0, total / 1048576.0, sp, eta)))
                    else:
                        self.events.put(("updprog", 0, "%s · 已下载 %.1f MB · %.2f MB/s" % (
                            _tag, got / 1048576.0, sp)))

                try:
                    got = fast_download(url, part, emit)
                    if got < 1024 * 1024:
                        raise Exception("安装包不完整")
                    self.events.put(("updprog", 100, "下载完成，正在替换程序…"))
                    try:
                        if os.path.exists(tmp):
                            os.remove(tmp)
                        os.rename(part, tmp)
                    except Exception:
                        shutil.copy2(part, tmp)
                    self.events.put(("apply", tmp))
                    ok = True
                    return
                except Exception as e:
                    dlog("更新下载失败(第%d次 %s): %s" % (attempt + 1, tag, e))
                    try:
                        if os.path.exists(part):
                            os.remove(part)
                    except Exception:
                        pass
                    time.sleep(1.0)
            if not ok:
                self.events.put(("updprogfail", "更新错误，请重试"))

        threading.Thread(target=work, daemon=True).start()

    def apply_update(self, new_exe):
        cur = sys.executable
        old = cur + ".old"
        try:
            if os.path.exists(old):
                os.remove(old)
            os.rename(cur, old)
            import shutil
            shutil.copy2(new_exe, cur)
            try:
                os.remove(new_exe)
            except Exception:
                pass
            subprocess.Popen([cur], cwd=os.path.dirname(cur), close_fds=True)
            self.root.destroy()
            os._exit(0)
        except Exception as e:
            self.logline("自更新失败: %s（可手动下载最新版覆盖当前程序）" % e)

    def choose_dir(self):
        d = filedialog.askdirectory(initialdir=self.dir_var.get() or self.outdir, title="选择保存目录")
        if d:
            self.outdir = d
            self.dir_var.set(d)
            self.cfg["outdir"] = d
            save_config(self.cfg)
            self.logline("保存目录: " + d)

    def open_dir(self):
        d = os.path.join(self.dir_var.get(), SUB_DIR)
        if not os.path.isdir(d):
            d = self.dir_var.get()
        if not os.path.isdir(d):
            messagebox.showwarning(APP_NAME, "目录不存在：" + d)
            return
        try:
            if os.name == "nt":
                os.startfile(d)  # noqa
            elif sys.platform == "darwin":
                subprocess.Popen(["open", d])
            else:
                subprocess.Popen(["xdg-open", d])
        except Exception as e:
            messagebox.showwarning(APP_NAME, "打不开目录: %s" % e)

    def clear(self):
        if self.busy:
            messagebox.showinfo(APP_NAME, "正在下载，先等它结束")
            return
        self.input.delete("1.0", "end")
        self.tasks = []
        for i in self.tree.get_children():
            self.tree.delete(i)
        self.logline("已清空")

    def recognize(self):
        text = self.input.get("1.0", "end").strip()
        urls = extract_urls(text)
        if not urls:
            messagebox.showinfo(APP_NAME, "没识别到链接")
            return
        self._fill_rows(urls)
        self.logline("识别中，正在读取标题 / 作者 / 时长…")
        for t in self.tasks:
            threading.Thread(target=self._recognize_one,
                             args=(t,), daemon=True).start()

    def _cookie_opts(self, opts):
        if self.cookiefile and os.path.isfile(self.cookiefile):
            opts["cookiefile"] = self.cookiefile
        elif self.cookie_profile and os.path.isdir(self.cookie_profile):
            # yt-dlp 要的是“用户数据目录”（Default 的上一级），不是 Default 本身
            root = self.cookie_profile
            if os.path.basename(os.path.normpath(root)).lower() == "default":
                root = os.path.dirname(os.path.normpath(root))
            opts["cookiesfrombrowser"] = (self.cookie_browser.lower(), root, None, None)
        elif self.cookie_browser and self.cookie_browser != "不使用":
            opts["cookiesfrombrowser"] = (self.cookie_browser.lower(), None, None, None)
        return opts


    def _douyin_headless_args(self):
        browser = self.cookie_browser if self.cookie_browser != "不使用" else (detect_default_browser() or "Edge")
        exe = find_browser_exe(browser)
        if exe is None:
            browser = "Chrome" if browser == "Edge" else "Edge"
            exe = find_browser_exe(browser)
        if exe is None:
            return None, None
        prof = login_profile_dir(browser)
        if self.cookie_profile and os.path.isdir(os.path.dirname(self.cookie_profile)):
            prof = os.path.dirname(self.cookie_profile)
        os.makedirs(prof, exist_ok=True)
        return exe, prof

    def douyin_resolve(self, url):
        """先用无头浏览器在登录态里取详情，失败返回 None"""
        try:
            if getattr(self, "_login_proc", None) is not None and self._login_proc.poll() is None:
                self._close_login_browser()  # 先关掉登录窗口，避免再多开一个抖音
        except Exception:
            pass
        exe, prof = self._douyin_headless_args()
        if not exe:
            return None
        return resolve_douyin_cdp(exe, prof, url)

    def _recognize_one(self, task):
        """只读解析：拿到标题、作者、时长，不下载"""
        try:
            from yt_dlp import YoutubeDL
        except Exception as e:
            self.events.put(("log", "✗ 解析组件没加载成功: %s" % e))
            self.events.put(("log", "  这是程序临时文件被系统清理了，关掉程序重新打开一次就好（登录状态不会丢）"))
            self.set_status(task.idx, "识别失败", 0.0)
            return
        self.set_status(task.idx, "识别中", 0.0)
        dlog("开始识别 %s" % task.url)
        if "douyin.com" in task.url:
            try:
                d = self.douyin_resolve(task.url)
            except Exception as e:
                d = None
                dlog("抖音解析异常: %s" % e)
                self.events.put(("log", "抖音解析没成功：%s" % (str(e)[:100])))
                self.events.put(("log", "  详细过程已写入 vd_log.txt（%LOCALAPPDATA%\\VDDesktop），识别不出请把这个文件发我"))
            if d:
                title = (d.get("title") or task.url).strip()
                author = d.get("author") or ""
                dur = human_time((d.get("dur") or 0) / 1000.0 if d.get("dur") else 0)
                like = d.get("like") or 0
                like_s = ("%d" % like) if like < 10000 else ("%.1f万" % (like / 10000.0))
                self.dy_direct[task.url] = d.get("play")
                self._dy_info_cache[task.url] = {"title": title, "author": author,
                                                 "dur": dur, "like": like_s}
                self.events.put(("meta", task.idx, title, author, dur))
                self.set_status(task.idx, "待下载", 0.0)
                self.events.put(("log", "✓ %s%s%s · 赞 %s" % (title,
                                 (" · " + author) if author else "",
                                 (" · " + dur) if dur else "", like_s)))
                return
        opts = self._cookie_opts({"quiet": True, "noprogress": True,
                                  "no_warnings": False, "retries": 3})
        try:
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(task.url, download=False)
            info = info or {}
            title = info.get("title") or task.url
            author = info.get("uploader") or info.get("channel") or info.get("creator") or ""
            dur = human_time(info.get("duration"))
            self.events.put(("meta", task.idx, title, author, dur))
            self.set_status(task.idx, "待下载", 0.0)
            self.events.put(("log", "✓ %s%s%s" % (title,
                             (" · " + author) if author else "",
                             (" · " + dur) if dur else "")))
        except Exception as e:
            msg = str(e).splitlines()[0][:100] if str(e) else "未知错误"
            dlog("yt-dlp 识别失败 %s → %s" % (task.url, msg))
            self.set_status(task.idx, "识别失败", 0.0)
            self.events.put(("log", "✗ %s → %s" % (task.url, msg)))
            if "cookie" in msg.lower() or "login" in msg.lower():
                self.events.put(("log", "  提示：抖音要登录态，点「扫码登录」登录一次再识别"))

    def _fill_rows(self, urls):
        for i in self.tree.get_children():
            self.tree.delete(i)
        self.tasks = []
        for i, u in enumerate(urls, 1):
            t = Task(i, u)
            self.tasks.append(t)
            self.tree.insert("", "end", iid=str(i),
                             values=(i, u[:70], "", "", "等待", "0%"))
        self.logline("识别到 %d 条链接" % len(urls))

    # ---------- 下载 ----------
    def start(self):
        if self.busy:
            messagebox.showinfo(APP_NAME, "已经在下载了")
            return
        text = self.input.get("1.0", "end").strip()
        urls = extract_urls(text)
        if not urls:
            messagebox.showinfo(APP_NAME, "先粘贴链接")
            return
        outdir = self.dir_var.get().strip()
        if not os.path.isdir(outdir):
            try:
                os.makedirs(outdir, exist_ok=True)
            except Exception as e:
                messagebox.showerror(APP_NAME, "建不了目录: %s" % e)
                return
        # 文件统一收进「视频下载器」子文件夹，避免和别的文件混在一起
        self.outdir = os.path.join(outdir, SUB_DIR)
        try:
            os.makedirs(self.outdir, exist_ok=True)
        except Exception as e:
            self.outdir = outdir
            self.logline("建子目录失败，直接存在所选目录: %s" % e)
        self.cfg["outdir"] = outdir
        self.quality = self.q_cb.get()
        self.cfg["quality"] = self.quality
        save_config(self.cfg)

        self._fill_rows(urls)
        self.busy = True
        self.cancelled = False
        for t in self.tasks:
            self.jobs.put(t)
        self.workers_left = min(2, len(self.tasks))
        for _ in range(self.workers_left):  # 两个并发
            threading.Thread(target=self._worker, daemon=True).start()
        self.logline("开始下载 %d 条 → %s" % (len(urls), outdir))

    @staticmethod
    def _format_for(quality):
        """按画质选格式：最高不限，1080p/720p 显式限高，优先视频流+音频流再回退单文件"""
        if quality == "1080p":
            return "bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b"
        if quality == "720p":
            return "bv*[height<=720]+ba/b[height<=720]/bv*+ba/b"
        return "bv*+ba/b"

    def _worker(self):
        try:
            from yt_dlp import YoutubeDL
        except Exception as e:
            self.events.put(("log", "✗ 解析组件没加载成功: %s" % e))
            self.events.put(("log", "  这是程序临时文件被系统清理了，关掉程序重新打开一次就好（登录状态不会丢）"))
            self._worker_done()
            return
        ff = find_ffmpeg()
        while not self.cancelled:
            try:
                task = self.jobs.get_nowait()
            except queue.Empty:
                self._worker_done()
                return
            idx = task.idx

            def hook(d):
                if d.get("status") == "downloading":
                    total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                    got = d.get("downloaded_bytes") or 0
                    if total:
                        p = max(0.0, min(1.0, float(got) / float(total)))
                        self.set_status(idx, "下载中", p)
                elif d.get("status") == "finished":
                    self.set_status(idx, "处理中", 1.0)

            direct = self.dy_direct.get(task.url)
            if direct:
                info0 = self._dy_info_cache.get(task.url) or {}
                title0 = info0.get("title") or "douyin"
                self.events.put(("meta", task.idx, title0, info0.get("author", ""), info0.get("dur", "")))
                self.set_status(task.idx, "下载中", 0.0)
                dopts = {
                    "outtmpl": os.path.join(self.outdir, "%(title).120B.%(ext)s"),
                    "format": "best",
                    "noprogress": True,
                    "quiet": True,
                    "no_warnings": True,
                    "progress_hooks": [hook],
                    "http_headers": {"User-Agent": UA_DESKTOP,
                                     "Referer": "https://www.douyin.com/"},
                    "overwrites": True,
                }
                try:
                    safe = re.sub(r'[\\/:*?"<>|]+', " ", title0).strip()[:80] or "douyin"
                    dopts["outtmpl"] = os.path.join(self.outdir, safe + ".%(ext)s")
                    with YoutubeDL(dopts) as ydl2:
                        ydl2.download([direct])
                    self.set_status(task.idx, "已完成", 1.0)
                    self.events.put(("log", "✓ %s" % title0))
                except Exception as e:
                    msg = str(e).splitlines()[0][:100] if str(e) else "未知错误"
                    self.set_status(task.idx, "失败: " + msg, 0.0)
                    self.events.put(("log", "✗ %s → %s" % (task.url, msg)))
                continue

            fmt = self._format_for(self.quality)
            opts = {
                "outtmpl": os.path.join(self.outdir, "%(title).120B [%(id)s].%(ext)s"),
                "format": fmt,
                "merge_output_format": "mp4",
                "noprogress": True,
                "quiet": True,
                "no_warnings": False,
                "progress_hooks": [hook],
                "retries": 10,
                "fragment_retries": 10,
                "concurrent_fragment_downloads": 4,
            }
            opts["format_sort"] = ["res", "vcodec:h264", "acodec:aac", "ext:mp4:m4a"]
            if ff:
                opts["ffmpeg_location"] = ff
            self._cookie_opts(opts)

            try:
                self.set_status(idx, "解析中", 0.0)
                with YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(task.url, download=False)
                    title = (info or {}).get("title") or task.url
                    dur = human_time((info or {}).get("duration"))
                    author = ((info or {}).get("uploader") or (info or {}).get("channel")
                              or (info or {}).get("creator") or "")
                    self.events.put(("meta", idx, title, author, dur))
                    ydl.download([task.url])
                self.set_status(idx, "已完成", 1.0)
                self.events.put(("log", "✓ %s" % title))
            except Exception as e:
                msg = str(e).splitlines()[0][:120] if str(e) else "未知错误"
                self.set_status(idx, "失败: " + msg, 0.0)
                self.events.put(("log", "✗ %s → %s" % (task.url, msg)))
                low = msg.lower()
                if "cookie" in low:
                    self.events.put(("log", "  提示：抖音/小红书需要登录。点顶部「扫码登录」登录一次即可，"
                                            "或导出一份 cookies.txt；选浏览器时记得先把浏览器完全关掉再点下载。"))

    def _worker_done(self):
        self.workers_left -= 1
        if self.workers_left <= 0:
            self.events.put(("done",))

    # ---------- 主线程刷新 ----------
    def _pump(self):
        try:
            while True:
                ev = self.events.get_nowait()
                kind = ev[0]
                if kind == "row":
                    idx, status, p = ev[1], ev[2], ev[3]
                    iid = str(idx)
                    pct = None if p is None else max(0, min(100, int(p * 100)))
                    if self.tree.exists(iid):
                        vals = list(self.tree.item(iid, "values"))
                        vals[4] = status + ((" %d%%" % pct) if (pct is not None and status == "下载中") else "")
                        if pct is not None:
                            vals[5] = "%d%%" % pct
                        self.tree.item(iid, values=vals)
                    if pct is not None:
                        try:
                            self.pbar["value"] = pct
                            self.plbl.config(text="%d%%" % pct)
                            if pct >= 100:
                                self.pbar["value"] = 0
                        except Exception:
                            pass
                elif kind == "meta":
                    idx, title, author, dur = ev[1], ev[2], ev[3], ev[4]
                    iid = str(idx)
                    if self.tree.exists(iid):
                        vals = list(self.tree.item(iid, "values"))
                        vals[1] = title[:70]
                        vals[2] = (author or "")[:16]
                        vals[3] = dur
                        self.tree.item(iid, values=vals)
                elif kind == "log":
                    self.logline(ev[1])
                elif kind == "updprog":
                    pct = max(0, min(100, int(ev[1])))
                    txt = "%d%%" % pct
                    if len(ev) > 2 and ev[2]:
                        txt = "%d%%  %s" % (pct, ev[2])
                    bar = getattr(self, "_updbar", None)
                    lbl = getattr(self, "_updlbl", None)
                    try:
                        if bar is not None and bar.winfo_exists():
                            bar["value"] = pct
                        if lbl is not None and lbl.winfo_exists():
                            lbl.config(text=txt)
                    except Exception:
                        pass
                    try:
                        self.pbar["value"] = pct
                        self.plbl.config(text="%d%%" % pct)
                    except Exception:
                        pass
                elif kind == "updprogfail":
                    self.close_upd_progress()
                    try:
                        self.pbar["value"] = 0
                        self.plbl.config(text="0%")
                    except Exception:
                        pass
                    if messagebox.askyesno("检查更新", ev[1] + "\n\n要不要用浏览器直接下载最新版？"):
                        u = (getattr(self, "_upd_urls", None) or [UPDATE_EXE])[0]
                        try:
                            import webbrowser
                            webbrowser.open(u)
                        except Exception:
                            pass
                elif kind == "update":
                    self.prompt_update(ev[1], ev[2], ev[3])
                elif kind == "uptodate":
                    self.upd_btn.config(text="已是最新")
                    if not self._silent_check:
                        messagebox.showinfo("检查更新", "已是最新版本 %s" % ev[1])
                elif kind == "upfail":
                    self.upd_btn.config(text="检查更新")
                    if not self._silent_check:
                        messagebox.showwarning("检查更新", "更新错误，请重试")
                elif kind == "apply":
                    self.close_upd_progress()
                    self.apply_update(ev[1])
                elif kind == "done":
                    self.busy = False
                    self.logline("全部结束 · 文件在 " + self.outdir)
        except queue.Empty:
            pass
        self.root.after(180, self._pump)

    def on_close(self):
        if self.busy and not messagebox.askyesno(APP_NAME, "还在下载，确定退出？"):
            return
        self.cancelled = True
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    try:
        if os.path.exists(LOG_PATH):
            os.remove(LOG_PATH)
    except Exception:
        pass
    dlog("启动 %s v%s" % (APP_NAME, APP_VER))
    cleanup_leftovers()
    _warm_imports()
    App().run()
