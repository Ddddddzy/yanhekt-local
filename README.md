# yanhekt-local

延河课堂（yanhekt.cn）录播本地化套件：**课程视频下载器** + **双屏同步播放器**。

两个单文件 Python 脚本，零第三方依赖（合并视频需要 ffmpeg）。

## 功能

### `yanhekt.py` — 录播下载器

- 按课程 ID 批量下载录播，支持课时筛选（全部 / 序号 / 区间）
- 每节课自动下载两路信号：电脑屏幕（`-VGA.mp4`）+ 教室摄像头（`-Video.mp4`）
- 分片并发下载、断点续下（`--skip`）、AES-128 加密流自动解密合并
- 自动处理延河课堂的签名与防盗链（`Xclient_Signature`、m3u8 路径哈希、分片逐个签名、video token 过期自动刷新）

```powershell
python yanhekt.py 64333                    # 列出课程课时
python yanhekt.py 64333 --all              # 全部课时，双路
python yanhekt.py 64333 --list 0 2 4       # 指定课时
python yanhekt.py 64333 --range 3 9        # 区间 [3,9)
python yanhekt.py 64333 --all --vga        # 只要屏幕路
python yanhekt.py 64333 --all --skip       # 增量补新课时
```

**获取课程 ID**：在延河课堂网站进入**课程详情页**（能看到课时目录的那个页面），地址栏形如

```
https://www.yanhekt.cn/course/64333
                            ↑
                        这串数字就是课程 ID
```

注意是 `/course/` 开头的**课程页**链接，不是 `/session/` 开头的视频播放页链接——后者是单个课时的 ID，传给它会查不到课时。

**认证**：课时列表接口需要登录态。浏览器登录延河课堂后按 `F12` → 控制台执行：

```javascript
JSON.parse(localStorage.auth).token
```

把输出（不含外层引号）写入脚本旁的 `auth.txt`，或用 `--auth` 参数 / `YANHEKT_AUTH` 环境变量传入。token 过期后重新取一次即可。

### `player.py` — 双屏同步播放器

复刻网页版的双屏体验，但播的是本地已下载的文件：

- 侧边栏按**课程目录分组**列出课时，`-VGA` / `-Video` 文件自动配对
- 视图切换：**双屏 / 仅屏幕(PPT) / 仅教室**（同官网单双屏切换）
- **单进度条**同步拖动两路，漂移自动校正；倍速 1–4x 同步
- **声轨切换**：教室路 / 屏幕路 / 两路都开 / 静音
- 快捷键：空格播放暂停、`←→` ±5s、`↑↓` ±30s、双击画面全屏
- 「⚙ 目录」里可以增删视频根目录（支持多个），配置存 `config.json`

```powershell
python player.py                  # 默认扫描 ./output，起 http://127.0.0.1:8901
python player.py D:\videos ...    # 也可以直接指定一个或多个目录
```

## 关于"去水印"

不需要做任何处理：**学号/姓名滚动水印是播放器的 DOM 覆盖层，不在视频流里**。同一份 CDN 流服务所有用户，按人烧录成本上不可行——下载下来的文件天然无水印。

## 依赖

- Python 3.8+（只用标准库）
- ffmpeg：放 PATH 或本目录 `ffmpeg.exe`；都没有时脚本会尝试用 `imageio-ffmpeg` 兜底
- Windows / macOS / Linux 均可

## 目录结构约定

```
output/
  64333-课程名-教师/            # 下载器按课程建目录
    第1周 星期二 第5大节-VGA.mp4    # 屏幕/PPT 路
    第1周 星期二 第5大节-Video.mp4  # 教室摄像头路
```

播放器靠 `-VGA` / `-Video` 后缀配对，课程分组按目录名。

## 说明

仅限下载**自己有权限观看的课程**用于个人学习；请勿传播。接口实现参考了 [AuYang261/BIT_yanhe_download](https://github.com/AuYang261/BIT_yanhe_download) 等前辈项目，致谢。
