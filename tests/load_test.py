"""Bounded real-source smoke/load probe. Never sends hardware tuning commands."""
import asyncio
import json
import subprocess
import sys
import time
import argparse
from urllib.parse import urlsplit
from aiohttp import ClientSession, WSMsgType

async def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('url',nargs='?',default='http://127.0.0.1:8080/')
    parser.add_argument('--clients',type=int,default=5)
    parser.add_argument('--seconds',type=int,default=10)
    parser.add_argument('--service',default='hamsdr')
    args=parser.parse_args()
    if not 1<=args.clients<=10 or not 5<=args.seconds<=60:parser.error('clients 1..10, seconds 5..60')
    url=args.url
    parts=urlsplit(url);origin=f'{parts.scheme}://{parts.netloc}'
    def stats():
        raw=subprocess.check_output(['systemctl','show',args.service,'-p','CPUUsageNSec','-p','MemoryCurrent'],text=True)
        return dict(line.split('=',1) for line in raw.strip().splitlines())
    async with ClientSession() as session:
        sockets=[]
        for i in range(args.clients):
            ws=await session.ws_connect(url+'ws',origin=origin)
            await ws.send_json({'type':'tune','mode':'LSB','frequency':7100000+i*1000})
            await ws.send_json({'type':'audio','enabled':True})
            sockets.append(ws)
        before=stats();start=time.monotonic()
        async def receive(ws):
            counts={7:0,2:0};size=0;pcm=0
            try:
                async with asyncio.timeout(args.seconds):
                    async for message in ws:
                        if message.type==WSMsgType.BINARY:
                            kind=message.data[0]
                            if kind in counts:counts[kind]+=1
                            if kind==2:pcm+=len(message.data)-1
                            size+=len(message.data)
            except asyncio.TimeoutError:pass
            return {'waterfall':counts[7],'audio':counts[2],'bytes':size,'audio_seconds':pcm/32000}
        received=await asyncio.gather(*(receive(ws) for ws in sockets))
        elapsed=time.monotonic()-start;after=stats()
        for ws in sockets:await ws.close()
        assert all(r['waterfall']>=args.seconds*6 and r['audio_seconds']>=args.seconds*.9 for r in received),received
        print(json.dumps({'seconds':elapsed,'clients':received,
            'cpu_percent_one_core':(int(after['CPUUsageNSec'])-int(before['CPUUsageNSec']))/1e9/elapsed*100,
            'cgroup_memory_mib':int(after['MemoryCurrent'])/1048576},indent=2))
asyncio.run(main())
