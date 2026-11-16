"""VPS web console; only exposed through HTTPS basic authentication."""
import json
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import URLError,HTTPError

PAGE=r'''<!doctype html><html lang="vi"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Minecraft Console</title><style>body{background:#10161d;color:#e4ebf5;font:16px system-ui;max-width:1100px;margin:32px auto;padding:0 20px}h1{font-size:26px}button,input{font:inherit;padding:12px;border-radius:7px;border:1px solid #465569}input{background:#172332;color:white;flex:1;min-width:120px}button{cursor:pointer;background:#93d8ab;color:#10161d}button.stop{background:#f7bc9c}pre{background:#090d13;padding:18px;height:55vh;overflow:auto;white-space:pre-wrap;font:13px monospace;border-radius:10px}form{display:flex;gap:10px}#state{color:#93d8ab}small{display:block;color:#aab7c8;margin:12px 0}</style>
<h1>Purpur 1.21.11 · Console</h1><p id="state">Đang kết nối…</p><small id="saved"></small>
<pre id="log"></pre><form id="cmd"><input id="command" placeholder="list, say Xin chào, whitelist add tên…" autocomplete="off" required><button>Gửi lệnh</button><button class="stop" type="button" id="stop">Dừng và lưu</button></form><small id="notice">Stop sẽ tắt server, lưu đủ thư mục lên VPS rồi đẩy vào repo riêng tư. Sau khi job hoàn tất, bấm Run workflow trên GitHub để chạy lại.</small>
<script>const $=id=>document.getElementById(id);let busy=false;async function refresh(){try{const r=await fetch('api/status',{cache:'no-store'});const s=await r.json();$('state').textContent=(s.phase||'offline')+(s.run_id?' · Job '+s.run_id:'');$('saved').textContent=s.published?'Đã lưu vào repo: '+s.published.commit.slice(0,12)+(s.published.final?' · bản cuối phiên':' · checkpoint'): 'Chưa có xác nhận commit';if(s.log){let near=$('log').scrollHeight-$('log').scrollTop-$('log').clientHeight<80;$('log').textContent=s.log.join('\n');if(near)$('log').scrollTop=$('log').scrollHeight;}}catch(e){$('state').textContent='Mất kết nối console';}}async function send(command){if(busy)return;busy=true;try{const r=await fetch('api/command',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({command})});const d=await r.json();if(!r.ok)throw Error(d.error||'Không gửi được lệnh');$('notice').textContent=command==='stop'?'Đã yêu cầu dừng. Chờ xác nhận bản cuối phiên đã lưu vào repo.':'Đã gửi: '+command;$('command').value='';}catch(e){$('notice').textContent=e.message;}finally{busy=false;}refresh();}$('cmd').onsubmit=e=>{e.preventDefault();send($('command').value)};$('stop').onclick=()=>send('stop');setInterval(refresh,2000);refresh();</script></html>'''

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def reply(self,code,body,kind='application/json'):
        body=body if isinstance(body,bytes) else json.dumps(body).encode()
        self.send_response(code);self.send_header('Content-Type',kind)
        self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(body)))
        self.end_headers();self.wfile.write(body)
    def do_GET(self):
        if self.path=='/':return self.reply(200,PAGE.encode(),'text/html; charset=utf-8')
        if self.path!='/api/status':return self.reply(404,{'error':'Not found'})
        try:
            with urlopen('http://127.0.0.1:25579/',timeout=3) as r:status=json.load(r)
        except (URLError,OSError):status={'phase':'offline','log':[]}
        saved=Path('/srv/mc-temp12111/published.json')
        if saved.exists():status['published']=json.loads(saved.read_text())
        self.reply(200,status)
    def do_POST(self):
        if self.path!='/api/command':return self.reply(404,{'error':'Not found'})
        # Caddy strips authentication before forwarding; block cross-site writes.
        origin=self.headers.get('Origin')
        host=self.headers.get('X-Forwarded-Host',self.headers.get('Host'))
        if origin and origin not in ('https://'+host,'http://'+host):
            return self.reply(403,{'error':'Origin mismatch'})
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=4096:raise ValueError('Invalid request size')
            payload=self.rfile.read(size)
            with urlopen(Request('http://127.0.0.1:25579/',data=payload,headers={'Content-Type':'application/json'}),timeout=4) as r:
                result=json.load(r)
            self.reply(200,result)
        except HTTPError as e:self.reply(e.code,{'error':e.read().decode()})
        except (URLError,OSError):self.reply(503,{'error':'Server offline hoặc đang kết thúc phiên'})
        except ValueError as e:self.reply(400,{'error':str(e)})

if __name__=='__main__':ThreadingHTTPServer(('127.0.0.1',18880),Handler).serve_forever()
