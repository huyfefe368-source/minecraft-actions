import asyncio
import importlib.util
import json
import os
import sys
from pathlib import Path

MODULE=Path(__file__).resolve().parents[1]/'runner_console.py'
spec=importlib.util.spec_from_file_location('runner_console', MODULE)
runner=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=runner
spec.loader.exec_module(runner)

def test_metrics_schema():
    metrics=runner.collect_metrics(os.getpid())
    assert metrics['ok'] is True
    for key in ('cpu_percent','memory_bytes','uptime_seconds','timestamp'):
        assert isinstance(metrics[key], (int,float))

def test_stop_request_flag(tmp_path,monkeypatch):
    monkeypatch.setenv('RUNNER_TEMP',str(tmp_path))
    monkeypatch.setattr(runner,'TOKEN','correct')
    fifo=tmp_path/'fifo.txt';fifo.write_text('')
    logfile=tmp_path/'server.log';logfile.write_text('ready')
    async def exercise():
        async def handle(reader, writer):
            await runner.handle(reader,writer,fifo,logfile,os.getpid())
        server=await asyncio.start_server(handle,'127.0.0.1',0)
        async with server:
            port=server.sockets[0].getsockname()[1]
            r,w=await asyncio.open_connection('127.0.0.1',port)
            w.write(b'{"token":"correct","op":"command","command":"stop"}\n')
            await w.drain()
            resp=json.loads(await r.readline())
            w.close(); await w.wait_closed()
            return resp
    assert asyncio.run(exercise())=={'ok':True}
    assert (tmp_path/'mc-stop-requested').exists()

def test_wrong_token_rejected(tmp_path,monkeypatch):
    monkeypatch.setattr(runner,'TOKEN','correct')
    fifo=tmp_path/'fifo.txt';fifo.write_text('')
    log=tmp_path/'server.log';log.write_text('ready')
    async def exercise():
        async def handler(r,w): await runner.handle(r,w,fifo,log,os.getpid())
        srv=await asyncio.start_server(handler,'127.0.0.1',0)
        async with srv:
            port=srv.sockets[0].getsockname()[1]
            r,w=await asyncio.open_connection('127.0.0.1',port)
            w.write(b'{"token":"bad","op":"logs"}\n');await w.drain()
            resp=json.loads(await r.readline())
            w.close();await w.wait_closed()
            return resp
    assert asyncio.run(exercise())=={'ok':False,'error':'unauthorized'}
