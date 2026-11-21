# 视频下载器 · 电脑版

Cangora 制作

粘贴分享文案自动只取链接，支持一次多条，**保存目录可自定义**（会记住上次选的目录）。
Windows 单文件 exe，内置 yt-dlp 与 ffmpeg，双击即可用，不需要装 Python。

## 下载

到 Releases 页面下载 `视频下载器.exe`（每次发版自动构建，最新版总在 Latest）。

## 功能

- 输入框粘整段分享文案（如「3.00 复制打开抖音，看看【xx的作品】… https://v.douyin.com/xxx/」），自动只取链接
- 一次可粘多条，逐条识别、两条并发下载，单条失败不影响后面的
- 「选择文件夹」自定义保存目录，配置记在 `%APPDATA%\VDDesktop\config.json`
- 任务表显示序号、标题、时长、状态、进度百分比
- 画质可选 最佳 / 720p
- 日志区可滚动，出问题复制日志即可
- 支持抖音、快手、小红书、B站、微博、西瓜、AcFun、知乎、网易云、YouTube、TikTok、Instagram、Twitter/X、Facebook、Vimeo、SoundCloud 等（以 yt-dlp 实际支持为准）

## 自己构建

```bash
pip install -r requirements.txt
pip install --upgrade yt-dlp
pyinstaller --noconfirm --onefile --windowed --name "视频下载器" --collect-all yt_dlp vd_desktop.py
```

产物在 `dist/视频下载器.exe`。想要音视频合并，把 `ffmpeg.exe` 放在 exe 同目录（程序会优先用同目录的 ffmpeg）。

不打包直接跑源码：`python vd_desktop.py`

## 自动构建（GitHub Actions）

`.github/workflows/build.yml`：
- 手动触发（Actions → build-windows-exe → Run workflow）→ 产物在 Actions 的 Artifacts 里
- 推送 `v*` 标签 → 自动构建并把 exe 传到对应 Release

## 已知限制

- 抖音 `/note/` 图文链接 yt-dlp 不支持，只处理视频
- 需要能连外网（抖音/B站等站点解析）
