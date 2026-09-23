#!/usr/bin/env python3
# player.py — 延河课堂本地双屏播放器
#
# 用法：
#   python player.py [视频目录 ...]      # 指定一个或多个根目录（默认 ./output）
#   然后浏览器打开 http://127.0.0.1:8901
#
# 功能：
#   - 侧边栏按课程（顶层目录）分组列出课时
#   - 同名 -VGA.mp4 / -Video.mp4 自动配对成双屏
#   - 视图切换：双屏 / 仅屏幕(PPT) / 仅教室摄像头（同延河课堂网页版）
#   - 单进度条同步拖两路、倍速同步、声轨可切（屏幕路/教室路/都开/静音）
#   - 目录在网页「设置」里增删，存 config.json 持久化
#
# 依赖：Python 3.8+ 标准库。

import json
import os
import re
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")
DEFAULT_PORT = 8901


def load_config(cli_roots):
    cfg = {"roots": [], "port": DEFAULT_PORT}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception:
            pass
    for r in cli_roots:
        r = os.path.abspath(r)
        if r not in cfg["roots"]:
            cfg["roots"].append(r)
    if not cfg["roots"]:
        cfg["roots"] = [os.path.abspath("./output")]
    cfg["roots"] = [r for r in cfg["roots"] if os.path.isdir(r)]
    return cfg


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


CFG = load_config(sys.argv[1:])

PAGE = r"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8"><title>延河课堂双屏播放</title>
<style>
  *{box-sizing:border-box;margin:0}
  body{font-family:system-ui,sans-serif;background:#141414;color:#eee;display:flex;height:100vh}
  #side{width:290px;overflow-y:auto;background:#1e1e1e;padding:8px;flex-shrink:0;display:flex;flex-direction:column}
  #side h3{font-size:14px;padding:8px;color:#aaa;display:flex;justify-content:space-between;align-items:center}
  #side h3 button{font-size:11px;padding:3px 8px}
  .course{margin:4px 0}
  .course-h{padding:7px 10px;font-size:13px;font-weight:600;cursor:pointer;border-radius:6px;background:#262626;user-select:none}
  .course-h:hover{background:#333}
  .course-h .cnt{color:#888;font-weight:400;font-size:11px;margin-left:6px}
  .items{padding-left:10px}
  .item{padding:7px 10px;border-radius:6px;cursor:pointer;font-size:13px;line-height:1.4}
  .item:hover{background:#333}.item.on{background:#2d5a8a}
  .item .sub{color:#999;font-size:11px}
  #main{flex:1;display:flex;flex-direction:column;min-width:0}
  #vids{flex:1;display:flex;gap:4px;padding:8px;min-height:0}
  .vbox{flex:1;position:relative;background:#000;display:flex;align-items:center;justify-content:center;min-width:0}
  .vbox.hide{display:none}
  video{width:100%;max-height:100%}
  .vtag{position:absolute;top:6px;left:8px;font-size:12px;background:#000a;padding:2px 8px;border-radius:4px;pointer-events:none}
  .novid{color:#555;font-size:13px}
  #ctrl{padding:10px 14px;background:#1e1e1e}
  #bar{width:100%;cursor:pointer}
  .row{display:flex;gap:10px;align-items:center;margin-top:6px;flex-wrap:wrap}
  button,select{background:#333;color:#eee;border:0;border-radius:5px;padding:5px 12px;cursor:pointer;font-size:13px}
  button:hover,select:hover{background:#444}
  button.on{background:#2d5a8a}
  #time{font-variant-numeric:tabular-nums;font-size:13px;color:#ccc}
  .sep{color:#555}
  #settings{display:none;position:fixed;inset:0;background:#000c;z-index:9;align-items:center;justify-content:center}
  #settings .panel{background:#222;border-radius:10px;padding:20px;width:560px;max-width:90vw}
  #settings h4{margin-bottom:10px;font-size:15px}
  .rrow{display:flex;gap:6px;margin:6px 0;align-items:center;font-size:13px}
  .rrow code{flex:1;background:#111;padding:5px 8px;border-radius:4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  #newroot{flex:1;background:#111;border:1px solid #444;color:#eee;border-radius:4px;padding:6px 8px}
  #settings .foot{margin-top:14px;text-align:right}
  .hint{font-size:12px;color:#888;margin-top:8px}
</style></head><body>
<div id="side">
  <h3>课时列表 <button id="btnSet">⚙ 目录</button></h3>
  <div id="list"></div>
</div>
<div id="main">
  <div id="vids">
    <div class="vbox" id="boxVga"><span class="vtag">屏幕 / PPT</span><video id="vvga"></video><span class="novid" id="nv1" style="display:none">本课无屏幕路</span></div>
    <div class="vbox" id="boxCam"><span class="vtag">教室摄像头</span><video id="vcam"></video><span class="novid" id="nv2" style="display:none">本课无摄像头路</span></div>
  </div>
  <div id="ctrl">
    <input id="bar" type="range" min="0" max="1000" value="0">
    <div class="row">
      <button id="play">▶ 播放</button>
      <span id="time">0:00 / 0:00</span>
      <span class="sep">|</span>
      <span>视图</span>
      <button class="view on" data-v="dual">双屏</button>
      <button class="view" data-v="vga">仅屏幕</button>
      <button class="view" data-v="cam">仅教室</button>
      <span class="sep">|</span>
      <span>声轨</span>
      <select id="audio">
        <option value="cam">教室（摄像头路）</option>
        <option value="vga">屏幕路</option>
        <option value="both">两路都开</option>
        <option value="none">静音</option>
      </select>
      <span>倍速</span>
      <select id="rate"><option>1</option><option>1.25</option><option selected>1.5</option><option>2</option><option>3</option><option>4</option></select>
      <span class="sep">|</span>
      <span style="font-size:12px;color:#888">空格=播放/暂停 ←→=±5s ↑↓=±30s 双击画面=全屏</span>
    </div>
  </div>
</div>
<div id="settings"><div class="panel">
  <h4>视频目录设置</h4>
  <div id="roots"></div>
  <div class="rrow"><input id="newroot" placeholder="粘贴目录绝对路径，如 D:\yanhekt-dl\output"><button id="addroot">添加</button></div>
  <div class="hint">目录里按「课程文件夹 / 课时名-VGA.mp4 / 课时名-Video.mp4」组织（下载器默认输出即是该结构）。改动即时生效。</div>
  <div class="foot"><button id="closeset">完成</button></div>
</div></div>
<script>
const vga = document.getElementById('vvga'), cam = document.getElementById('vcam');
const boxVga = document.getElementById('boxVga'), boxCam = document.getElementById('boxCam');
const bar = document.getElementById('bar'), timeEl = document.getElementById('time');
const playBtn = document.getElementById('play'), audioSel = document.getElementById('audio'), rateSel = document.getElementById('rate');
let master = cam, seeking = false;

function fmt(s){ if(!isFinite(s)) s=0; s=Math.floor(s); return Math.floor(s/3600)?Math.floor(s/3600)+':'+String(Math.floor(s/60)%60).padStart(2,'0')+':'+String(s%60).padStart(2,'0'):Math.floor(s/60)+':'+String(s%60).padStart(2,'0'); }
function dur(){ return Math.max(vga.duration||0, cam.duration||0); }
function now(){ return master.currentTime || 0; }
function sync(){
  const slave = master === cam ? vga : cam;
  if (slave.src && slave.readyState && Math.abs(slave.currentTime - master.currentTime) > 0.35)
    slave.currentTime = master.currentTime;
}
function applyRate(){ const r = parseFloat(rateSel.value); vga.playbackRate = r; cam.playbackRate = r; }
function applyAudio(){
  vga.muted = !(audioSel.value==='vga'||audioSel.value==='both');
  cam.muted = !(audioSel.value==='cam'||audioSel.value==='both');
}
function toggle(){
  if (vga.paused && cam.paused){ sync(); vga.play().catch(()=>{}); cam.play().catch(()=>{}); playBtn.textContent='⏸ 暂停'; }
  else { vga.pause(); cam.pause(); playBtn.textContent='▶ 播放'; }
}
function seekTo(t){ if(vga.src) vga.currentTime=t; if(cam.src) cam.currentTime=t; }

cam.addEventListener('timeupdate', ()=>{
  if(!seeking){ bar.value = dur()? Math.round(now()/dur()*1000) : 0; timeEl.textContent = fmt(now())+' / '+fmt(dur()); }
  sync();
});
vga.addEventListener('timeupdate', ()=>{ if(master===vga) cam.dispatchEvent(new Event('noop')); });
bar.addEventListener('input', ()=>{ seeking=true; const t=bar.value/1000*dur(); timeEl.textContent=fmt(t)+' / '+fmt(dur()); });
bar.addEventListener('change', ()=>{ seekTo(bar.value/1000*dur()); seeking=false; });
playBtn.onclick = toggle; rateSel.onchange = applyRate; audioSel.onchange = applyAudio;
document.querySelectorAll('.view').forEach(b=>b.onclick=()=>{
  document.querySelectorAll('.view').forEach(x=>x.classList.remove('on'));
  b.classList.add('on');
  const v=b.dataset.v;
  boxVga.classList.toggle('hide', v==='cam');
  boxCam.classList.toggle('hide', v==='vga');
});
cam.addEventListener('click', toggle); vga.addEventListener('click', toggle);
cam.addEventListener('dblclick', ()=>cam.requestFullscreen&&cam.requestFullscreen());
vga.addEventListener('dblclick', ()=>vga.requestFullscreen&&vga.requestFullscreen());
document.addEventListener('keydown', e=>{
  if(e.target.tagName==='SELECT'||e.target.tagName==='INPUT') return;
  if(e.code==='Space'){ e.preventDefault(); toggle(); }
  else if(e.code==='ArrowLeft'){ seekTo(Math.max(0, now()-5)); }
  else if(e.code==='ArrowRight'){ seekTo(now()+5); }
  else if(e.code==='ArrowUp'){ seekTo(now()+30); }
  else if(e.code==='ArrowDown'){ seekTo(Math.max(0, now()-30)); }
});

// ---- 课时列表：按课程分组 ----
function vurl(v){ return '/video/'+v.r+'/'+encodeURIComponent(v.p); }
function loadList(){
  fetch('/api/list').then(r=>r.json()).then(groups=>{
    const box = document.getElementById('list'); box.innerHTML='';
    groups.forEach(g=>{
      const wrap = document.createElement('div'); wrap.className='course';
      const h = document.createElement('div'); h.className='course-h';
      h.innerHTML = '▾ ' + g.course + `<span class="cnt">${g.items.length}节</span>`;
      const items = document.createElement('div'); items.className='items';
      h.onclick = ()=>{ const hid=items.style.display==='none'; items.style.display=hid?'':'none'; h.innerHTML=(hid?'▾ ':'▸ ')+g.course+`<span class="cnt">${g.items.length}节</span>`; };
      g.items.forEach(p=>{
        const d = document.createElement('div'); d.className='item';
        d.innerHTML = p.name + `<div class="sub">${p.vga?'🖥 屏幕':''}${p.cam?' 📷 教室':''}${p.miss?' ⚠缺'+p.miss:''}</div>`;
        d.onclick = ()=>{
          document.querySelectorAll('.item').forEach(x=>x.classList.remove('on'));
          d.classList.add('on');
          vga.src = p.vga ? vurl(p.vga) : ''; cam.src = p.cam ? vurl(p.cam) : '';
          document.getElementById('nv1').style.display = p.vga?'none':'';
          document.getElementById('nv2').style.display = p.cam?'none':'';
          master = cam.src ? cam : vga;
          vga.load(); cam.load(); applyAudio(); applyRate(); toggle();
        };
        items.appendChild(d);
      });
      wrap.appendChild(h); wrap.appendChild(items); box.appendChild(wrap);
    });
  });
}

// ---- 目录设置 ----
const setEl = document.getElementById('settings');
document.getElementById('btnSet').onclick = ()=>{ setEl.style.display='flex'; renderRoots(); };
document.getElementById('closeset').onclick = ()=>{ setEl.style.display='none'; loadList(); };
function renderRoots(){
  fetch('/api/config').then(r=>r.json()).then(c=>{
    const box = document.getElementById('roots'); box.innerHTML='';
    c.roots.forEach((r,i)=>{
      const d = document.createElement('div'); d.className='rrow';
      d.innerHTML = `<code title="${r}">${r}</code><button data-i="${i}">删</button>`;
      d.querySelector('button').onclick = ()=>saveRoots(c.roots.filter((_,j)=>j!==i));
      box.appendChild(d);
    });
  });
}
function saveRoots(roots){
  fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({roots})})
    .then(()=>renderRoots());
}
document.getElementById('addroot').onclick = ()=>{
  const v = document.getElementById('newroot').value.trim(); if(!v) return;
  fetch('/api/config').then(r=>r.json()).then(c=>{ c.roots.push(v); saveRoots(c.roots); document.getElementById('newroot').value=''; });
};

loadList(); applyAudio(); applyRate();
</script></body></html>"""


def list_groups():
    groups = {}
    for ridx, root in enumerate(CFG["roots"]):
        for dirpath, _, files in os.walk(root):
            for fn in files:
                if not fn.lower().endswith(".mp4"):
                    continue
                m = re.match(r"^(.*)-(VGA|Video)\.mp4$", fn, re.I)
                if m:
                    name, kind = m.group(1), m.group(2).lower()
                else:
                    name, kind = os.path.splitext(fn)[0], "other"
                course = os.path.relpath(dirpath, root)
                gkey = (ridx, course)
                g = groups.setdefault(gkey, {"course": course if course != "." else "(根目录)",
                                             "items": {}})
                g["items"].setdefault(name, {})[kind] = {"r": ridx,
                    "p": os.path.relpath(os.path.join(dirpath, fn), root).replace(os.sep, "/")}
    out = []
    for gkey in sorted(groups, key=lambda k: (k[0], k[1])):
        g = groups[gkey]
        items = []
        for name in sorted(g["items"]):
            v = g["items"][name]
            it = {"name": name, "vga": v.get("vga"), "cam": v.get("video")}
            if v.get("other"):
                it["cam"] = it["cam"] or v["other"]
            if not it["vga"] or not it["cam"]:
                it["miss"] = "VGA" if not it["vga"] else "摄像头"
            items.append(it)
        out.append({"course": g["course"], "items": items})
    return out


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def do_GET(self):
        path = urllib.parse.unquote(urllib.parse.urlparse(self.path).path)
        if path == "/":
            b = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
        elif path == "/api/list":
            self._json(list_groups())
        elif path == "/api/config":
            self._json({"roots": CFG["roots"]})
        elif path.startswith("/video/"):
            self.serve_video(path[len("/video/"):])
        else:
            self.send_error(404)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/config":
            try:
                body = json.loads(self._body().decode("utf-8"))
                roots = [os.path.abspath(r) for r in body.get("roots", []) if os.path.isdir(r)]
                CFG["roots"] = roots or CFG["roots"]
                save_config(CFG)
                self._json({"ok": True, "roots": CFG["roots"]})
            except Exception as e:
                self._json({"ok": False, "error": str(e)})
        else:
            self.send_error(404)

    def serve_video(self, tail):
        # tail = "<rootIdx>/<relpath>"
        try:
            ridx_s, rel = tail.split("/", 1)
            root = CFG["roots"][int(ridx_s)]
        except Exception:
            return self.send_error(404)
        fp = os.path.normpath(os.path.join(root, rel.replace("/", os.sep)))
        if not fp.startswith(root) or not os.path.isfile(fp):
            return self.send_error(404)
        size = os.path.getsize(fp)
        rng = self.headers.get("Range")
        start, end = 0, size - 1
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)", rng)
            if m:
                if m.group(1):
                    start = int(m.group(1))
                if m.group(2):
                    end = min(int(m.group(2)), size - 1)
        if start >= size:
            return self.send_error(416)
        self.send_response(206 if rng else 200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        with open(fp, "rb") as f:
            f.seek(start)
            remain = end - start + 1
            while remain > 0:
                chunk = f.read(min(1024 * 1024, remain))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remain -= len(chunk)


if __name__ == "__main__":
    print("视频目录：")
    for r in CFG["roots"]:
        print("  -", r)
    print(f"播放器：http://127.0.0.1:{CFG['port']}")
    ThreadingHTTPServer(("127.0.0.1", CFG["port"]), H).serve_forever()
