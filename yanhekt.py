#!/usr/bin/env python3
# yanhekt.py — 延河课堂（yanhekt.cn）录播下载器
#
# 用法：
#   python yanhekt.py 64333                 # 列出课程全部课时
#   python yanhekt.py 64333 --all           # 下载全部课时
#   python yanhekt.py 64333 --list 0 2 4    # 下载指定序号课时
#   python yanhekt.py 64333 --range 3 9     # 下载 [3,9) 区间课时
#   python yanhekt.py 64333 --all --vga     # 只下电脑屏幕信号（默认屏幕+摄像头都下）
#   python yanhekt.py 64333 --all --skip    # 跳过已存在的文件
#
# 认证：session 列表接口要求登录态。在浏览器登录延河课堂后，于地址栏执行
#   javascript:alert(JSON.parse(localStorage.auth).token)
# 把弹出的 token 用 --auth 传入，或写入脚本同目录的 auth.txt，或设环境变量 YANHEKT_AUTH。
#
# 依赖：Python 3.8+ 标准库；合并需要 ffmpeg（PATH 里没有时会尝试 imageio_ffmpeg，
# 实在不行自动 pip install imageio-ffmpeg）。

import argparse
import concurrent.futures
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import json
from hashlib import md5

MAGIC = "1138b69dfef641d9d7ba49137d2d4875"  # 前端 main.js 里的固定盐
API = "https://cbiz.yanhekt.cn"

BASE_HEADERS = {
    "Origin": "https://www.yanhekt.cn",
    "Referer": "https://www.yanhekt.cn/",
    "xdomain-client": "web_user",
    "Xclient-Version": "v1",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
}

_state = threading.local()   # 每线程一个 video token，401/403 时各自刷新
_print_lock = threading.Lock()


def log(msg):
    with _print_lock:
        print(msg, flush=True)


def signature():
    ts = str(int(time.time()))
    return ts, md5((MAGIC + "_v1_" + ts).encode()).hexdigest()


def api_headers(auth):
    h = dict(BASE_HEADERS)
    h["Xclient-Timestamp"], h["Xclient-Signature"] = signature()
    if auth:
        h["Authorization"] = "Bearer " + auth
    return h


def http_get(url, auth=None, timeout=30):
    req = urllib.request.Request(url, headers=api_headers(auth))
    return urllib.request.urlopen(req, timeout=timeout)


def get_json(url, auth=None):
    with http_get(url, auth) as r:
        return json.loads(r.read().decode("utf-8"))


def video_token(auth):
    if not getattr(_state, "token", None):
        data = get_json(API + "/v1/auth/video/token?id=0", auth)
        if not data.get("data"):
            raise RuntimeError("获取 video token 失败，auth 可能已过期")
        _state.token = data["data"]["token"]
    return _state.token


def refresh_token(auth):
    _state.token = None
    return video_token(auth)


def sign_url(url, auth):
    ts, sig = signature()
    sep = "&" if "?" in url else "?"
    return (url + sep + "Xvideo_Token=" + video_token(auth)
            + "&Xclient_Timestamp=" + ts + "&Xclient_Signature=" + sig
            + "&Xclient_Version=v1&Platform=yhkt_user")


def encrypt_url(url):
    """延河课堂防盗链：在路径最后一段前插入 md5(magic+'_100')。"""
    parts = url.split("/")
    parts.insert(-1, md5((MAGIC + "_100").encode()).hexdigest())
    return "/".join(parts)


def resolve(base, ref):
    """按 HLS 规则把相对 ref 解析成绝对 URL。"""
    if ref.startswith("http"):
        return ref
    if ref.startswith("/"):
        p = urllib.parse.urlparse(base)
        return f"{p.scheme}://{p.netloc}{ref}"
    return base.rsplit("/", 1)[0] + "/" + ref


def fetch_text(url, auth):
    """签名资源拉取；401/403 时刷新 video token 重试（token 约 10 分钟过期）。"""
    last = None
    for _ in range(3):
        try:
            with http_get(sign_url(url, auth), auth, timeout=30) as r:
                return r.read().decode("utf-8", "replace"), r.geturl()
        except Exception as e:
            last = e
            if getattr(e, "code", None) in (401, 403):
                refresh_token(auth)
                continue
            raise
    raise last


def fetch_bytes(url, auth, signed=False, retries=5):
    last = None
    for attempt in range(retries + 1):
        try:
            u = sign_url(url, auth) if signed else url
            with http_get(u, auth, timeout=60) as r:
                return r.read()
        except Exception as e:
            last = e
            code = getattr(e, "code", None)
            if code in (401, 403) and signed:
                refresh_token(auth)
            if attempt < retries:
                time.sleep(min(5, 0.3 * (attempt + 1)))
    raise RuntimeError(f"下载失败 {url}: {last}")


def sanitize(name):
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip() or "untitled"


def parse_playlist(text):
    """返回 (segments, key_line)；若是 master playlist 返回 None。"""
    if "#EXT-X-STREAM-INF" in text:
        return None
    segs, key_line = [], None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            if "EXT-X-KEY" in line and "URI=" in line:
                key_line = line
        else:
            segs.append(line)
    return segs, key_line


def pick_variant(text, base_url):
    """master playlist 里选带宽最高的一路。"""
    best_bw, best = -1, None
    bw = 0
    for line in text.splitlines():
        line = line.strip()
        m = re.match(r"#EXT-X-STREAM-INF:.*BANDWIDTH=(\d+)", line)
        if m:
            bw = int(m.group(1))
            continue
        if line and not line.startswith("#"):
            if bw > best_bw:
                best_bw, best = bw, line
            bw = 0
    if best is None:
        raise RuntimeError("master playlist 解析失败")
    return resolve(base_url, best)


def download_stream(m3u8_url, out_mp4, auth, workers, ffmpeg):
    """下载一路 HLS 流到 out_mp4。返回 True 成功。"""
    tmp = tempfile.mkdtemp(prefix="yhkt_")
    try:
        url = encrypt_url(m3u8_url)  # 防盗链路径混淆，缺了就是 403
        for _ in range(3):  # 最多跟 3 层 master playlist
            text, url = fetch_text(url, auth)  # fetch_text 内部签名；url 取跳转后的最终地址
            parsed = parse_playlist(text)
            if parsed:
                segs, key_line = parsed
                break
            url = pick_variant(text, url)
        else:
            raise RuntimeError("playlist 嵌套过深")

        if not segs:
            raise RuntimeError("播放列表为空")

        # AES-128 key：延河课堂的 key URI 不需要签名
        key_path = None
        if key_line:
            m = re.search(r'URI=["\']([^"\']+)', key_line)
            key_path = os.path.join(tmp, "key")
            with open(key_path, "wb") as f:
                f.write(fetch_bytes(resolve(url, m.group(1)), auth))

        # 并发下分片；每个请求单独签名
        def one(i):
            data = fetch_bytes(resolve(url, segs[i]), auth, signed=True)
            p = os.path.join(tmp, f"{i:06d}.ts")
            with open(p, "wb") as f:
                f.write(data)
            return i

        done = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            for _ in ex.map(one, range(len(segs))):
                done += 1
                if done % 50 == 0 or done == len(segs):
                    log(f"    {done}/{len(segs)}")

        # 本地 m3u8：key 和分片都指本地相对路径
        lines = ["#EXTM3U", "#EXT-X-VERSION:3"]
        if key_line:
            pre = key_line.split("URI=")[0]
            post = key_line.rsplit('"', 1)[-1] if key_line.rstrip().endswith('"') else ""
            lines.append(f'{pre}URI="key"{post}')
        lines.append("#EXT-X-TARGETDURATION:10")
        for i in range(len(segs)):
            lines.append("#EXTINF:10.0,")
            lines.append(f"{i:06d}.ts")
        lines.append("#EXT-X-ENDLIST")
        playlist_path = os.path.join(tmp, "index.m3u8")
        with open(playlist_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        if ffmpeg:
            subprocess.run(
                [ffmpeg, "-y", "-loglevel", "error",
                 "-allowed_extensions", "ALL",
                 "-protocol_whitelist", "file,crypto,data",
                 "-i", playlist_path,
                 "-c", "copy", "-bsf:a", "aac_adtstoasc", out_mp4],
                check=True)
        else:
            # 无 ffmpeg 且流未加密：裸拼 .ts 也能播
            if key_path:
                raise RuntimeError("该视频是 AES 加密流，必须装 ffmpeg 才能合并")
            with open(out_mp4 + ".ts", "wb") as out:
                for i in range(len(segs)):
                    with open(os.path.join(tmp, f"{i:06d}.ts"), "rb") as f:
                        shutil.copyfileobj(f, out)
            log(f"    无 ffmpeg，输出未封装的 .ts：{out_mp4}.ts")
            return True
        return True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def find_ffmpeg():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    local = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ffmpeg.exe")
    if os.path.exists(local):
        return local
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "imageio-ffmpeg"],
                       check=True, timeout=300)
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def get_auth(args):
    auth = args.auth or os.environ.get("YANHEKT_AUTH", "")
    auth_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "auth.txt")
    if not auth and os.path.exists(auth_file):
        with open(auth_file, encoding="utf-8") as f:
            auth = f.read().strip()
    if auth and args.auth:
        with open(auth_file, "w", encoding="utf-8") as f:  # 记住，下次免输
            f.write(auth)
    return auth


def main():
    ap = argparse.ArgumentParser(description="延河课堂录播下载器")
    ap.add_argument("course", help="课程 ID，即 yanhekt.cn/course/<id> 里的数字")
    sel = ap.add_mutually_exclusive_group()
    sel.add_argument("-A", "--all", action="store_true", help="下载全部课时")
    sel.add_argument("-L", "--list", type=int, nargs="+", metavar="i",
                     help="按序号下载，如 --list 0 2 4")
    sel.add_argument("-R", "--range", type=int, nargs=2, metavar=("a", "b"),
                     help="按区间下载 [a,b)，如 --range 3 9")
    typ = ap.add_mutually_exclusive_group()
    typ.add_argument("--vga", action="store_true", help="只下电脑屏幕信号")
    typ.add_argument("--video", action="store_true", help="只下教室摄像头信号")
    ap.add_argument("--skip", action="store_true", help="跳过已存在的文件")
    ap.add_argument("--dir", default="./output", help="输出目录（默认 ./output）")
    ap.add_argument("--workers", type=int, default=24, help="分片并发数（默认 24）")
    ap.add_argument("--auth", default="", help="延河课堂登录 token")
    args = ap.parse_args()

    auth = get_auth(args)

    course = get_json(f"{API}/v1/course?id={args.course}&with_professor_badges=true", auth)
    if course.get("code") not in (0, "0"):
        sys.exit(f"课程查询失败：{course.get('message')}（course id 应取自 /course/ 页面，不是 /session/）")
    cname = course["data"]["name_zh"].strip()
    prof = course["data"]["professors"][0]["name"].strip() if course["data"]["professors"] else "未知教师"

    res = get_json(f"{API}/v2/course/session/list?course_id={args.course}", auth)
    sessions = res.get("data") or []
    if not sessions:
        sys.exit(
            "课时列表为空——多半是需要登录态。\n"
            "在浏览器登录延河课堂后，地址栏执行：\n"
            "  javascript:alert(JSON.parse(localStorage.auth).token)\n"
            "（粘贴时浏览器会吃掉 javascript: 前缀，需手动补回）\n"
            "把弹出的 token 用 --auth 传入，或写入脚本旁的 auth.txt。"
        )

    for i, s in enumerate(sessions):
        vids = (s.get("videos") or [{}])[0]
        sig = "/".join(k for k in ("vga" if vids.get("vga") else "",
                                   "cam" if vids.get("main") else "") if k) or "无视频"
        print(f"[{i:2d}] {s['title']}  ({sig})")

    if args.all:
        idxs = list(range(len(sessions)))
    elif args.list:
        idxs = args.list
    elif args.range:
        idxs = list(range(args.range[0], args.range[1]))
    else:
        print("\n未选择课时。加 --all / --list / --range 下载。")
        return

    out_dir = os.path.join(args.dir, sanitize(f"{args.course}-{cname}-{prof}"))
    os.makedirs(out_dir, exist_ok=True)
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        print("⚠️  未找到 ffmpeg，且无法自动安装 imageio-ffmpeg；加密流会失败，未加密流降级为 .ts 拼接")

    signals = []
    if not args.video:
        signals.append(("vga", "VGA"))
    if not args.vga:
        signals.append(("main", "Video"))

    ok, fail = 0, 0
    for i in idxs:
        if not (0 <= i < len(sessions)):
            print(f"[{i}] 序号越界，跳过")
            continue
        s = sessions[i]
        title = sanitize(s["title"])
        vids = (s.get("videos") or [{}])[0]
        for key, tag in signals:
            m3u8 = vids.get(key)
            if not m3u8:
                print(f"[{i}] {title} 无 {tag} 信号，跳过")
                continue
            out = os.path.join(out_dir, f"{title}-{tag}.mp4")
            if args.skip and os.path.exists(out):
                print(f"[{i}] {title}-{tag} 已存在，跳过")
                continue
            print(f"[{i}] 下载 {title}-{tag}")
            try:
                if download_stream(m3u8, out, auth, args.workers, ffmpeg):
                    ok += 1
                    print(f"    ✔ {out}")
            except Exception as e:
                fail += 1
                print(f"    ✘ {e}")

    print(f"\n完成：{ok} 成功，{fail} 失败。输出目录：{os.path.abspath(out_dir)}")


if __name__ == "__main__":
    main()
