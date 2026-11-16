"""VPS web console; only exposed through HTTPS basic authentication."""
import json,subprocess
from urllib.parse import urlsplit
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import URLError,HTTPError

PAGE=r'''<!doctype html><html lang="vi"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Minecraft Console</title><style>body{background:#10161d;color:#e4ebf5;font:16px system-ui;max-width:1100px;margin:32px auto;padding:0 20px}h1{font-size:26px}button,input{font:inherit;padding:12px;border-radius:7px;border:1px solid #465569}input{background:#172332;color:white;flex:1;min-width:120px}button{cursor:pointer;background:#93d8ab;color:#10161d}button.stop{background:#f7bc9c}pre{background:#090d13;padding:18px;height:55vh;overflow:auto;white-space:pre-wrap;font:13px monospace;border-radius:10px}form{display:flex;gap:10px;flex-wrap:wrap}#state{color:#93d8ab}small{display:block;color:#aab7c8;margin:12px 0}</style>
<h1>Purpur 1.21.11 · Console</h1><p id="state">Đang kết nối…</p><small id="saved"></small>
<div style="display:flex;gap:10px;flex-wrap:wrap"><button type="button" onclick="control('start')">Bật server / job</button><button type="button" onclick="control('stop')">Dừng server / job và lưu</button><label><input type="checkbox" id="always" onchange="control(this.checked?'enable':'disable')"> Chạy 24/7</label></div><small id="job"></small><pre id="log"></pre><form id="cmd"><input id="command" placeholder="list, say Xin chào, whitelist add tên…" autocomplete="off" required><button>Gửi lệnh</button><button class="stop" type="button" id="stop">Dừng và lưu</button></form><small id="notice">Dừng sẽ tắt 24/7, lưu server rồi kết thúc job. Bật 24/7 để tự chuyển job sau khoảng 5 giờ 30 phút; có gián đoạn khi lưu và khởi động.</small>
<script>const $=id=>document.getElementById(id);let busy=false;async function refresh(){try{const r=await fetch('/api/status',{cache:'no-store',credentials:'same-origin'});if(!r.ok)throw Error('HTTP '+r.status+' — thử tải lại và đăng nhập');const s=await r.json();$('always').checked=!!s.control?.enabled;$('job').textContent=s.control?.message||'';$('state').textContent=({offline:'Server đang tắt',starting:'Đang khởi động',running:'Đang chạy',checkpoint:'Đang đồng bộ',stopping:'Đang dừng và lưu',saving:'Đang lưu'}[s.phase]||s.phase||'Không rõ trạng thái')+(s.run_id?' · Job '+s.run_id:'');$('saved').textContent=s.published?'Đã lưu vào repo: '+s.published.commit.slice(0,12)+(s.published.final?' · bản cuối phiên':' · checkpoint'): 'Chưa có xác nhận commit';if(s.log){let near=$('log').scrollHeight-$('log').scrollTop-$('log').clientHeight<80;$('log').textContent=s.log.join('\n');if(near)$('log').scrollTop=$('log').scrollHeight;}}catch(e){$('state').textContent='Không tải được trạng thái: '+e.message;}}async function send(command){if(busy)return;busy=true;try{const r=await fetch('/api/command',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({command})});const d=await r.json();if(!r.ok)throw Error(d.error||'Không gửi được lệnh');$('notice').textContent=command==='stop'?'Đã yêu cầu dừng. Chờ xác nhận bản cuối phiên đã lưu vào repo.':'Đã gửi: '+command;$('command').value='';}catch(e){$('notice').textContent=e.message;}finally{busy=false;}refresh();}async function control(action){try{const r=await fetch('/api/control',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({action})});const d=await r.json();if(!r.ok)throw Error(d.error||'HTTP '+r.status);$('notice').textContent=d.message||'Đã gửi yêu cầu';}catch(e){$('notice').textContent=e.message;}refresh();}$('cmd').onsubmit=e=>{e.preventDefault();send($('command').value)};$('stop').onclick=()=>control('stop');setInterval(refresh,2000);refresh();</script></html>'''

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def reply(self,code,body,kind='application/json'):
        body=body if isinstance(body,bytes) else json.dumps(body).encode()
        self.send_response(code);self.send_header('Content-Type',kind)
        self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(body)))
        self.end_headers();self.wfile.write(body)
    def do_GET(self):
        if urlsplit(self.path).path=='/':return self.reply(200,PAGE.encode(),'text/html; charset=utf-8')
        if urlsplit(self.path).path!='/api/status':return self.reply(404,{'error':'Not found'})
        try:
            with urlopen('http://127.0.0.1:25579/',timeout=3) as r:status=json.load(r)
        except (URLError,OSError):status={'phase':'offline','log':[]}
        saved=Path('/srv/mc-temp12111/published.json')
        if saved.exists():status['published']=json.loads(saved.read_text())
        controller=Path('/srv/mc-temp12111/controller.json')
        status['control']=json.loads(controller.read_text()) if controller.exists() else {'enabled':False}
        if status['phase']=='offline' and status['control'].get('active'):status['phase']='starting'
        self.reply(200,status)
    def do_POST(self):
        if urlsplit(self.path).path not in ('/api/command','/api/control'):return self.reply(404,{'error':'Not found'})
        # Caddy strips authentication before forwarding; block cross-site writes.
        origin=self.headers.get('Origin')
        host=self.headers.get('X-Forwarded-Host',self.headers.get('Host'))
        if origin and origin not in ('https://'+host,'http://'+host):
            return self.reply(403,{'error':'Origin mismatch'})
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=4096:raise ValueError('Invalid request size')
            payload=self.rfile.read(size)
            request=json.loads(payload)
            if not isinstance(request,dict):raise ValueError('Invalid request')
            if urlsplit(self.path).path=='/api/control' or str(request.get('command','')).strip().lower()=='stop':
                action=request.get('action') if urlsplit(self.path).path=='/api/control' else 'stop'
                if action not in ('start','stop','enable','disable'):raise ValueError('Invalid action')
                r=subprocess.run(['sudo','-n','/opt/mc-temp12111/control',action],capture_output=True,text=True,timeout=40)
                return self.reply(200 if r.returncode==0 else 502,json.loads(r.stdout or '{"error":"Controller unavailable"}'))
            with urlopen(Request('http://127.0.0.1:25579/',data=payload,headers={'Content-Type':'application/json'}),timeout=4) as r:
                result=json.load(r)
            self.reply(200,result)
        except HTTPError as e:self.reply(e.code,{'error':e.read().decode()})
        except (URLError,OSError):self.reply(503,{'error':'Server offline hoặc đang kết thúc phiên'})
        except ValueError as e:self.reply(400,{'error':str(e)})
        except subprocess.TimeoutExpired:self.reply(504,{'error':'GitHub phản hồi chậm; kiểm tra lại trạng thái trước khi bấm lại'})

if __name__=='__main__':ThreadingHTTPServer(('127.0.0.1',18880),Handler).serve_forever()
