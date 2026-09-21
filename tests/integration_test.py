# SPDX-License-Identifier: GPL-3.0-only
"""Protocol, multi-user isolation, restart, and invalid-control regression tests."""
import asyncio
import contextlib
import json
import math
from pathlib import Path
import struct
import sys
from types import SimpleNamespace
import unittest
import tempfile
import uuid
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aiohttp import ClientSession, WSServerHandshakeError, WSMsgType, web
from server import application, GATEWAY
from opus_codec import OPUS_AVAILABLE, OpusDecoder
from waterfall_codec import decode

class RadioTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.args = SimpleNamespace(demo=True, source_host='127.0.0.1',source_port=1231,max_clients=5,origin='')
        self.args.full_quality_sessions_per_ip=5
        self.args.max_bandwidth_kbps_per_ip=1000000
        self.args.digimodes=False
        self.args.site_config=Path(__file__).resolve().parents[1]/'site.example.toml'
        self.temp = tempfile.TemporaryDirectory()
        self.args.database=Path(self.temp.name)/'community.sqlite3'
        await self.start_server()
        self.session=ClientSession()
        self.sockets=[]

    async def start_server(self):
        self.app=application(self.args)
        self.runner=web.AppRunner(self.app)
        await self.runner.setup()
        site=web.TCPSite(self.runner,'127.0.0.1',0)
        await site.start()
        port=site._server.sockets[0].getsockname()[1]
        self.url=f'http://127.0.0.1:{port}'

    async def asyncTearDown(self):
        for ws in self.sockets: await ws.close()
        await self.session.close()
        await self.runner.cleanup()
        self.temp.cleanup()

    async def connect(self):
        ws=await self.session.ws_connect(self.url+'/ws',origin=self.url)
        self.sockets.append(ws)
        await ws.send_json({'type':'hello','protocol':1,'waterfall':2})
        reply=await self.event(ws,'hello')
        self.assertEqual((reply['protocol'],reply['waterfall']),(1,2))
        return ws

    async def event(self, ws, kind, predicate=lambda x: True):
        async with asyncio.timeout(10):
            while True:
                msg = await ws.receive()
                if msg.type == WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    if data.get('type') == kind and predicate(data):
                        return data

    async def identify(self, ws, name, key=None):
        key = key or uuid.uuid4().hex
        await ws.send_json({'type':'identify','name':name,'key':key})
        result=await self.event(ws,'identified')
        return key,result['id']

    async def test_presence_chat_log_restart_and_idempotence(self):
        alice,bob=await self.connect(),await self.connect()
        key,alice_id=await self.identify(alice,'TESTER')
        await self.identify(bob,'OYENTE')
        await self.tune(alice,'USB',7101000)
        presence=await self.event(bob,'presence',lambda x:any(u['id']==alice_id and u['frequency']==7101000 for u in x['users']))
        self.assertEqual(next(u['name'] for u in presence['users'] if u['id']==alice_id),'TESTER')
        self.assertNotIn(key,json.dumps(presence))
        request={'type':'chat','request_id':uuid.uuid4().hex,'text':'<img src=x onerror=alert(1)> Señal recibida'}
        await alice.send_json(request)
        received=await self.event(bob,'event')
        ack=await self.event(alice,'ack')
        self.assertEqual(received['event'],ack['event'])
        self.assertEqual(ack['event']['frequency'],7101000)
        await alice.send_json(request)
        repeated=await self.event(alice,'ack')
        self.assertEqual(ack['event']['id'],repeated['event']['id'])
        await alice.send_json({'type':'log','request_id':uuid.uuid4().hex,'text':'Local test','call':'TEST-CALL'})
        logged=await self.event(alice,'ack')
        self.assertEqual(logged['event']['call'],'TEST-CALL')
        self.assertEqual(logged['event']['kind'],'log')
        await alice.close()
        gone=await self.event(bob,'presence',lambda x:all(u['id']!=alice_id for u in x['users']))
        self.assertEqual(len(gone['users']),1)
        # Restart only this isolated server and reopen the same on-disk DB.
        await bob.close();await self.runner.cleanup();await self.start_server()
        restored=await self.connect()
        history=await self.event(restored,'history',lambda x:x['kind']=='chat')
        self.assertEqual([e['id'] for e in history['events']],[ack['event']['id']])
        log=await self.event(restored,'history',lambda x:x['kind']=='log')
        self.assertEqual(log['events'][0]['id'],logged['event']['id'])
        await self.identify(restored,'TESTER',key)
        await restored.send_json(request)
        repeated=await self.event(restored,'ack')
        self.assertEqual(repeated['event']['id'],ack['event']['id'])
        unicode_message='📻'*500
        await restored.send_json({'type':'chat','request_id':uuid.uuid4().hex,'text':unicode_message})
        unicode_ack=await self.event(restored,'ack')
        self.assertEqual(unicode_ack['event']['text'],unicode_message)

    async def test_community_validation_and_flood_limit(self):
        ws=await self.connect()
        await self.identify(ws,'Prueba')
        for update in [
            {'type':'identify','key':uuid.uuid4().hex,'name':'Cambio de clave'},
            {'type':'chat','request_id':uuid.uuid4().hex,'text':'a'*501},
            {'type':'chat','request_id':uuid.uuid4().hex,'text':'control\u202e'},
            {'type':'history','kind':'private'},
            {'type':'history','before':float('nan')},
            {'type':'waterfall','preference':'broken','profile':'raw'},
            {'type':'waterfall-speed','divisor':3},
            {'type':'waterfall-speed','divisor':'high'},
            {'type':'audio-profile','profile':'mp3'},
            {'type':'audio-profile','profile':'original'},
            {'type':'audio-profile','profile':'opus-high'},
            {'type':'audio-profile','profile':'opus-low'},
            {'type':'audio','enabled':'yes'},
            {'type':'tune','nr':99}, {'type':'tune','notch':'yes'}]:
            await ws.send_json(update);await self.event(ws,'error')
        for i in range(4):
            await ws.send_json({'type':'chat','request_id':uuid.uuid4().hex,'text':str(i)})
            await self.event(ws,'ack')
        await ws.send_json({'type':'chat','request_id':uuid.uuid4().hex,'text':'flood'})
        error=await self.event(ws,'error')
        self.assertIn('Espera',error['message'])
        async with self.session.get(self.url+'/community.sqlite3') as response:self.assertEqual(response.status,404)

    async def tune(self,ws,mode,frequency):
        await ws.send_json({'type':'tune','mode':mode,'frequency':frequency})
        async with asyncio.timeout(10):
            while True:
                msg=await ws.receive()
                if msg.type==WSMsgType.TEXT and json.loads(msg.data).get('type')=='tuned':return

    async def collect(self,ws,n=8000):
        await ws.send_json({'type':'audio-profile','profile':'raw'})
        await self.event(ws,'audio-profile')
        await ws.send_json({'type':'audio','enabled':True})
        await self.event(ws,'audio-state')
        audio=[]; rows=0
        async with asyncio.timeout(12):
            while len(audio)<n:
                msg=await ws.receive()
                if msg.type==WSMsgType.BINARY:
                    if msg.data[0]==7:
                        self.assertGreater(len(msg.data),17)
                        rows+=1
                    elif msg.data[0]==2:
                        payload=msg.data[1:]
                        audio.extend(struct.unpack('<'+'h'*(len(payload)//2),payload))
        return audio,rows

    async def test_audio_subscription_stops_network_pcm(self):
        ws=await self.connect()
        await ws.send_json({'type':'audio-profile','profile':'raw'});await self.event(ws,'audio-profile')
        await ws.send_json({'type':'audio','enabled':True});await self.event(ws,'audio-state')
        async with asyncio.timeout(3):
            while True:
                message=await ws.receive()
                if message.type==WSMsgType.BINARY and message.data[0]==2:break
        await ws.send_json({'type':'audio','enabled':False});await self.event(ws,'audio-state')
        deadline=asyncio.get_running_loop().time()+1
        while asyncio.get_running_loop().time()<deadline:
            try:message=await asyncio.wait_for(ws.receive(),.2)
            except asyncio.TimeoutError:continue
            self.assertFalse(message.type==WSMsgType.BINARY and message.data[0] in (2,10,11))

    @unittest.skipUnless(OPUS_AVAILABLE, "system libopus unavailable")
    async def test_three_audio_profiles(self):
        expected={"raw":(2,513,513,256,16000),"balanced":(10,6,205,320,16000),
                  "mobile":(11,6,105,320,16000)}
        for profile,(kind,minimum,maximum,count,rate) in expected.items():
            ws=await self.connect()
            await self.tune(ws,'USB',7100000)
            await ws.send_json({'type':'audio-profile','profile':profile})
            reply=await self.event(ws,'audio-profile')
            self.assertEqual((reply['profile'],reply['rate']),(profile,rate))
            await ws.send_json({'type':'audio','enabled':True});await self.event(ws,'audio-state')
            async with asyncio.timeout(3):
                while True:
                    message=await ws.receive()
                    if message.type!=WSMsgType.BINARY or message.data[0] not in (2,10,11):continue
                    self.assertEqual(message.data[0],kind)
                    self.assertTrue(minimum<=len(message.data)<=maximum,len(message.data))
                    if kind==2:
                        samples=struct.unpack('<'+'h'*count,message.data[1:])
                    else:
                        if 'decoder' not in locals() or decoder_profile!=profile:
                            decoder,decoder_profile=OpusDecoder(),profile
                        samples=decoder.decode(message.data[5:])
                    if max(samples)-min(samples)>100:break
            self.assertEqual(len(samples),count)
            await ws.send_json({'type':'audio', 'enabled':False});await self.event(ws,'audio-state')

    async def test_multiuser_audio_and_engine_restart(self):
        modes=[('USB',7100000),('LSB',7090000),('AM',7108000),('USB',7100000),('LSB',7090000)]
        for mode,freq in modes:
            ws=await self.connect();await self.tune(ws,mode,freq)
        collected=await asyncio.gather(*(self.collect(ws) for ws in self.sockets))
        for audio,rows in collected:
            values=audio[3000:8000]
            rms=math.sqrt(sum(x*x for x in values)/len(values))/32768
            # Independent spectral measurement on the network PCM, not engine internals.
            def power(hz):
                real=sum(x*math.cos(2*math.pi*hz*i/16000) for i,x in enumerate(values))
                imag=sum(x*math.sin(2*math.pi*hz*i/16000) for i,x in enumerate(values))
                return math.hypot(real,imag)
            self.assertGreater(rms,.02)
            self.assertGreater(power(1000),power(2100)*20)
            self.assertGreater(rows,0)
        gateway=self.app[GATEWAY]
        old=gateway.process.pid
        gateway.process.kill()  # Isolated demo subprocess only.
        async with asyncio.timeout(10):
            while not gateway.process or gateway.process.pid==old or gateway.source!='demo':await asyncio.sleep(.05)
        audio,rows=await self.collect(self.sockets[0],16000)
        self.assertGreater(sum(x*x for x in audio[-4000:]),0)
        self.assertGreater(gateway.restarts,0)
        with self.assertRaises(WSServerHandshakeError) as caught:
            await self.session.ws_connect(self.url+'/ws',origin=self.url)
        self.assertEqual(caught.exception.status,503)

    async def test_four_waterfall_profiles_and_independent_rows(self):
        sockets=[await self.connect() for _ in range(4)]
        profiles=['slow','low','balanced','high']
        for ws,profile in zip(sockets,profiles):
            await ws.send_json({'type':'waterfall','preference':profile,'profile':profile})
            reply=await self.event(ws,'waterfall-profile')
            self.assertEqual(reply['profile'],profile)
        counts=[0,0,0,0];sequences=[[],[],[],[]]
        async def rows(index,ws):
            async with asyncio.timeout(6):
                while counts[index]<5:
                    msg=await ws.receive()
                    if msg.type!=WSMsgType.BINARY:continue
                    if msg.data[0]==7:
                        code,sequence,lower,span,data=decode(msg.data[1:])
                        self.assertEqual((code,lower,span,len(data)),((7,4,5,6)[index],6588500,1024000,(1024,1024,2048,4096)[index]))
                        counts[index]+=1;sequences[index].append(sequence)
        await asyncio.gather(*(rows(i,ws) for i,ws in enumerate(sockets)))
        self.assertTrue(all(b>a for values in sequences for a,b in zip(values,values[1:])))
        await sockets[1].send_json({'type':'waterfall-view','zoom':64,'center':7100000})
        async with asyncio.timeout(3):
            while True:
                msg=await sockets[1].receive()
                if msg.type==WSMsgType.BINARY and msg.data[0]==7:
                    _,_,lower,span,data=decode(msg.data[1:])
                    if lower==7092000 and span==16000:break
        self.assertEqual((lower,span,len(data)),(7092000,16000,1024))
        await sockets[0].send_json({'type':'waterfall','preference':'high','profile':'low'})
        await self.event(sockets[0],'error')

    async def test_server_side_waterfall_speeds(self):
        sockets=[await self.connect() for _ in range(3)]
        slow=await self.connect()
        await slow.send_json({'type':'waterfall','preference':'slow','profile':'slow'})
        await self.event(slow,'waterfall-profile')
        fast=await self.connect()
        await fast.send_json({'type':'waterfall','preference':'high','profile':'high'})
        await self.event(fast,'waterfall-profile')
        await fast.send_json({'type':'waterfall-speed','divisor':'high'})
        fast_reply=await self.event(fast,'waterfall-speed')
        self.assertEqual((fast_reply['divisor'],fast_reply['fps']),('high',11.7))
        divisors=(1,2,6)
        for ws,divisor in zip(sockets,divisors):
            await ws.send_json({'type':'waterfall-speed','divisor':divisor})
            reply=await self.event(ws,'waterfall-speed')
            self.assertEqual(reply['divisor'],divisor)
        async def sequences(ws):
            found=[]
            deadline=asyncio.get_running_loop().time()+4
            while asyncio.get_running_loop().time()<deadline:
                try:msg=await asyncio.wait_for(ws.receive(),min(.5,deadline-asyncio.get_running_loop().time()))
                except asyncio.TimeoutError:continue
                if msg.type==WSMsgType.BINARY and msg.data[0]==7:found.append(decode(msg.data[1:])[1])
            return found
        received=await asyncio.gather(*(sequences(ws) for ws in sockets),sequences(slow),sequences(fast))
        for values,divisor,minimum in zip(received,divisors,(20,10,3)):
            self.assertGreaterEqual(len(values),minimum)
        self.assertGreater(len(received[0]),len(received[1])*1.5)
        self.assertGreater(len(received[1]),len(received[2])*2)
        self.assertGreaterEqual(len(received[3]),18)
        self.assertLess(len(received[3]),len(received[0]))
        self.assertGreaterEqual(len(received[4]),40)
        self.assertGreater(len(received[4]),len(received[0])*1.3)

    async def test_origins_controls_and_assets(self):
        for origin in ['null','https://untrusted.example']:
            with self.assertRaises(WSServerHandshakeError) as caught:
                await self.session.ws_connect(self.url+'/ws',origin=origin)
            self.assertEqual(caught.exception.status,403)
        async with self.session.get(self.url+'/server.py') as response:self.assertEqual(response.status,404)
        async with self.session.get(self.url+'/') as response:
            self.assertEqual(response.status,200)
            self.assertIn("script-src 'self'",response.headers['Content-Security-Policy'])
            self.assertIn("base-uri 'none'",response.headers['Content-Security-Policy'])
            self.assertEqual(response.headers['Cross-Origin-Opener-Policy'],'same-origin')
            self.assertIn('microphone=()',response.headers['Permissions-Policy'])
        async with self.session.get(self.url+'/site-config.js') as response:
            self.assertEqual(response.status,200)
            self.assertIn('window.hamSdrSiteConfig=',await response.text())
        async with self.session.get(self.url+'/site-logo') as response:
            self.assertEqual(response.status,404)
        ws=await self.connect()
        for control in [{'type':'tune','frequency':float('nan')},{'type':'tune','frequency':1},
                        {'type':'tune','mode':'bogus'},{'type':'tune','low':0,'high':1},[]]:
            await ws.send_json(control)
            async with asyncio.timeout(3):
                while True:
                    message=await ws.receive()
                    if message.type==WSMsgType.TEXT and json.loads(message.data).get('type')=='error':break
        await self.tune(ws,'USB',7100000)
        audio,_=await self.collect(ws)
        self.assertGreater(max(audio),1000)

    async def test_optional_digital_audio_stream(self):
        ws=await self.connect()
        await ws.send_json({'type':'digital-mode','mode':'FT8'})
        error=await self.event(ws,'error')
        self.assertIn('Digimodos',error['message'])
        self.app[GATEWAY].args.digimodes=True
        await ws.send_json({'type':'digital-mode','mode':'FT8'})
        reply=await self.event(ws,'digital-mode')
        self.assertEqual((reply['mode'],reply['rate']),('FT8',12000))
        await self.tune(ws,'USB',7100000)
        await ws.send_json({'type':'audio','enabled':True});await self.event(ws,'audio-state')
        async with asyncio.timeout(4):
            while True:
                msg=await ws.receive()
                if msg.type==WSMsgType.BINARY and msg.data[0]==12:
                    mode_code,sequence,timestamp_us,count=struct.unpack('<BIQH',msg.data[1:16])
                    self.assertEqual(mode_code,1)
                    self.assertEqual(sequence,0)
                    self.assertGreater(timestamp_us,0)
                    self.assertEqual(count*2,len(msg.data)-16)
                    self.assertEqual(count,192)
                    break
        await ws.send_json({'type':'audio','enabled':False});await self.event(ws,'audio-state')
        deadline=asyncio.get_running_loop().time()+1
        while asyncio.get_running_loop().time()<deadline:
            try:message=await asyncio.wait_for(ws.receive(),.2)
            except asyncio.TimeoutError:continue
            self.assertFalse(message.type==WSMsgType.BINARY and message.data[0]==12)

    async def test_receiverbook_status_and_confirmation_tag(self):
        gateway = self.app[GATEWAY]
        token = "a" * 64
        gateway.receiverbook = {"enable": True, "confirmation": token,
            "tag": f'<meta name="receiverbook-confirmation" content="{token}">' }
        gateway.station = {"description": "Test HamSDR", "email": "operator@example.test",
                           "qth": "AA00aa", "mobile_page": "/", "flag": "",
                           "flag_description": "Station flag"}
        gateway.bands = [{"enable": True, "center_frequency_khz": 7100.5,
                          "sample_rate_khz": 1024.0, "antenna": "Test antenna"}]
        async with self.session.get(self.url+'/~~orgstatus') as response:
            self.assertEqual(response.status,200)
            status = await response.text()
            self.assertIn("Description: Test HamSDR\n", status)
            self.assertIn("Bands: 1\n", status)
            self.assertIn("Band: 0 7100.500 1024.000 Test antenna\n", status)
        async with self.session.get(self.url+'/') as response:
            document = await response.text()
            self.assertEqual(document.count('name="receiverbook-confirmation"'), 1)
            self.assertIn(f'content="{token}"', document)

    async def test_per_ip_connection_limit_and_trusted_proxy(self):
        gateway=self.app[GATEWAY]
        gateway.args.max_clients_per_ip=2
        first,second=await self.connect(),await self.connect()
        with self.assertRaises(WSServerHandshakeError) as caught:
            await self.session.ws_connect(self.url+'/ws',origin=self.url)
        self.assertEqual(caught.exception.status,429)
        await first.close();await second.close()

        gateway.args.max_clients_per_ip=1
        gateway.args.trusted_proxy='127.0.0.1'
        one=await self.session.ws_connect(self.url+'/ws',origin=self.url,headers={'X-HamSDR-Client-IP':'192.0.2.1'})
        two=await self.session.ws_connect(self.url+'/ws',origin=self.url,headers={'X-HamSDR-Client-IP':'192.0.2.2'})
        self.sockets.extend((one,two))
        for ws in (one,two):
            await ws.send_json({'type':'hello','protocol':1,'waterfall':2})
            await self.event(ws,'hello')

    async def test_protocol_negotiation_rejects_incompatible_clients(self):
        ws=await self.session.ws_connect(self.url+'/ws',origin=self.url)
        self.sockets.append(ws)
        await ws.send_json({'type':'hello','protocol':99,'waterfall':2})
        async with asyncio.timeout(3):
            while not ws.closed:
                await ws.receive()
        self.assertEqual(ws.close_code,1002)

    async def test_shared_ip_resource_profiles(self):
        gateway=self.app[GATEWAY]
        gateway.args.max_clients_per_ip=4
        gateway.args.full_quality_sessions_per_ip=2
        gateway.args.max_bandwidth_kbps_per_ip=1000
        first,second=await self.connect(),await self.connect()
        await first.send_json({'type':'waterfall','preference':'high','profile':'high'})
        self.assertEqual((await self.event(first,'waterfall-profile'))['profile'],'high')
        await first.send_json({'type':'audio-profile','profile':'raw'})
        self.assertEqual((await self.event(first,'audio-profile'))['profile'],'raw')

        third=await self.connect()
        self.assertEqual((await self.event(first,'waterfall-profile'))['profile'],'balanced')
        self.assertEqual((await self.event(first,'audio-profile'))['profile'],'balanced')
        limit=await self.event(first,'resource-limit')
        self.assertEqual((limit['waterfall_max'],limit['audio_max']),('balanced','balanced'))
        await third.send_json({'type':'waterfall','preference':'high','profile':'high'})
        self.assertIn('hasta balanced',(await self.event(third,'error'))['message'])
        await third.send_json({'type':'audio-profile','profile':'raw'})
        self.assertIn('hasta balanced',(await self.event(third,'error'))['message'])

        gateway.address_limit_stage['127.0.0.1']=2
        gateway.enforce_address_limits('127.0.0.1')
        self.assertEqual((await self.event(first,'waterfall-profile'))['profile'],'low')
        self.assertEqual((await self.event(first,'audio-profile'))['profile'],'mobile')

        gateway.address_limit_stage.pop('127.0.0.1',None)
        gateway.address_bandwidth_samples.pop('127.0.0.1',None)
        gateway.observe_address_bandwidth('127.0.0.1',1100)
        gateway.observe_address_bandwidth('127.0.0.1',1100)
        self.assertNotIn('127.0.0.1',gateway.address_limit_stage)
        gateway.observe_address_bandwidth('127.0.0.1',1100)
        self.assertEqual(gateway.address_limit_stage['127.0.0.1'],1)
        gateway.observe_address_bandwidth('127.0.0.1',900)
        self.assertEqual(gateway.address_limit_stage['127.0.0.1'],1)
        for _ in range(3):gateway.observe_address_bandwidth('127.0.0.1',700)
        self.assertNotIn('127.0.0.1',gateway.address_limit_stage)

    async def test_cw_carrier_frequency_and_narrow_filter(self):
        ws=await self.connect()
        for lo,hi in [(450,950),(600,800)]:
            await ws.send_json({'type':'tune','mode':'CW','frequency':7101000,'low':lo,'high':hi})
            await self.event(ws,'tuned')
            audio,_=await self.collect(ws)
            values=audio[3000:]
            def level(hz):
                return abs(sum(x*complex(math.cos(2*math.pi*hz*i/16000),math.sin(2*math.pi*hz*i/16000)) for i,x in enumerate(values)))
            self.assertGreater(level(700),level(1400)*30)
            self.assertGreater(max(values),1000)

if __name__=='__main__':unittest.main()
