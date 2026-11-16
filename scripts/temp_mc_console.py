"""VPS web console; only exposed through HTTPS basic authentication."""
import json,subprocess
from urllib.parse import urlsplit
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import URLError,HTTPError

from temp_mc_page import PAGE

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
        if 'minecraft_running' not in status:status['minecraft_running']=False
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
            if urlsplit(self.path).path=='/api/control':
                action=request.get('action') if urlsplit(self.path).path=='/api/control' else 'stop'
                if action not in ('start','restart','stop','mc_start','mc_restart','mc_stop','enable','disable'):raise ValueError('Invalid action')
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
