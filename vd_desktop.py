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
import json
import queue
import threading
import subprocess
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

APP_NAME = "视频下载器"
APP_VER = "1.0.0"
AUTHOR = "Cangora 制作"

BG = "#0d1117"
CARD = "#161b22"
FG = "#e6edf3"
SUB = "#8b949e"
BLUE = "#2f81f7"
GREEN = "#3fb950"
RED = "#e5534b"

URL_PATTERN = re.compile(r'https?://[^\s<>"\'）)】\]，,;；]+', re.I)

SUPPORT = ("抖音 · 快手 · 小红书 · 微博 · B站 · 西瓜视频 · AcFun · 知乎 · 网易云 · "
           "YouTube · TikTok · Instagram · Twitter/X · Facebook · Vimeo · SoundCloud")


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


class App(object):
    def __init__(self):
        self.cfg = load_config()
        self.outdir = self.cfg.get("outdir") or default_dir()
        self.quality = self.cfg.get("quality") or "最佳"
        self.tasks = []
        self.jobs = queue.Queue()
        self.events = queue.Queue()
        self.busy = False
        self.cancelled = False

        self.root = tk.Tk()
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
        s.configure("Treeview.Heading", background="#21262d", foreground=SUB,
                    borderwidth=0, font=("Microsoft YaHei UI", 9))
        s.map("Treeview", background=[("selected", "#1f6feb")])
        s.configure("green.Horizontal.TProgressbar", troughcolor="#21262d",
                    background=GREEN, borderwidth=0)
        s.configure("TCombobox", fieldbackground=CARD, background=CARD, foreground=FG)

    def _build(self):
        # 顶部标题行
        head = tk.Frame(self.root, bg=BG)
        head.pack(fill="x", padx=18, pady=(16, 6))
        tk.Label(head, text=APP_NAME, bg=BG, fg=FG,
                 font=("Microsoft YaHei UI", 20, "bold")).pack(side="left")
        tk.Label(head, text="  电脑版 · 本地运行 · 可自定义保存目录", bg=BG, fg=SUB,
                 font=("Microsoft YaHei UI", 10)).pack(side="left", pady=(8, 0))
        tk.Label(head, text="v" + APP_VER, bg=BG, fg=SUB,
                 font=("Microsoft YaHei UI", 9)).pack(side="right", pady=(8, 0))

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
        self.q_cb = ttk.Combobox(bar, values=["最佳", "720p"], width=8, state="readonly")
        self.q_cb.set(self.quality)
        self.q_cb.pack(side="left")

        # 任务表
        mid = tk.Frame(self.root, bg=BG)
        mid.pack(fill="both", expand=True, padx=18, pady=(4, 6))
        cols = ("no", "title", "dur", "status", "bar")
        self.tree = ttk.Treeview(mid, columns=cols, show="headings", height=8)
        for c, t, w, a in (("no", "序号", 50, "center"), ("title", "标题", 430, "w"),
                           ("dur", "时长", 70, "center"), ("status", "状态", 150, "w"),
                           ("bar", "进度", 150, "w")):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor=a)
        sb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

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

    def choose_dir(self):
        d = filedialog.askdirectory(initialdir=self.dir_var.get() or self.outdir, title="选择保存目录")
        if d:
            self.outdir = d
            self.dir_var.set(d)
            self.cfg["outdir"] = d
            save_config(self.cfg)
            self.logline("保存目录: " + d)

    def open_dir(self):
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

    def _fill_rows(self, urls):
        for i in self.tree.get_children():
            self.tree.delete(i)
        self.tasks = []
        for i, u in enumerate(urls, 1):
            t = Task(i, u)
            self.tasks.append(t)
            self.tree.insert("", "end", iid=str(i),
                             values=(i, u[:80], "", "等待", "0%"))
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
        self.outdir = outdir
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

    def _worker(self):
        try:
            from yt_dlp import YoutubeDL
        except Exception as e:
            self.events.put(("log", "✗ 缺少 yt-dlp 组件: %s" % e))
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

            fmt = ("bv*+ba/b" if self.quality == "最佳"
                   else "b[height<=720]/bv*[height<=720]+ba/b")
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
            if ff:
                opts["ffmpeg_location"] = ff

            try:
                self.set_status(idx, "解析中", 0.0)
                with YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(task.url, download=False)
                    title = (info or {}).get("title") or task.url
                    dur = human_time((info or {}).get("duration"))
                    self.events.put(("meta", idx, title, dur))
                    ydl.download([task.url])
                self.set_status(idx, "已完成", 1.0)
                self.events.put(("log", "✓ %s" % title))
            except Exception as e:
                msg = str(e).splitlines()[0][:120] if str(e) else "未知错误"
                self.set_status(idx, "失败: " + msg, 0.0)
                self.events.put(("log", "✗ %s → %s" % (task.url, msg)))

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
                    if self.tree.exists(iid):
                        vals = list(self.tree.item(iid, "values"))
                        vals[3] = status
                        if p is not None:
                            vals[4] = "%d%%" % int(p * 100)
                        self.tree.item(iid, values=vals)
                elif kind == "meta":
                    idx, title, dur = ev[1], ev[2], ev[3]
                    iid = str(idx)
                    if self.tree.exists(iid):
                        vals = list(self.tree.item(iid, "values"))
                        vals[1] = title[:80]
                        vals[2] = dur
                        self.tree.item(iid, values=vals)
                elif kind == "log":
                    self.logline(ev[1])
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
    App().run()
