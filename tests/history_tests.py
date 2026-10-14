# SPDX-License-Identifier: GPL-3.0-only
"""Cursor pagination, durable backup, size limiting and retry uniqueness."""
import asyncio
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from community import History


class HistoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_posting_limits_persist_and_retries_do_not_spend_allowance(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'community.sqlite3'
            clock=1800000000.25
            event={'kind':'chat','time':int(clock),'name':'Test','text':'Hello',
                   'frequency':7100000,'mode':'USB','call':'','client_key':'a'*32,'request_id':'1'*32}
            h=History(path)
            with patch('community.time.time',return_value=clock) as now:
                await h.open()
                try:
                    saved,created=await h.append(event,enforce_limits=True)
                    self.assertTrue(created)
                    repeated,created=await h.append(event,enforce_limits=True)
                    self.assertFalse(created)
                    self.assertEqual(repeated['id'],saved['id'])
                    with self.assertRaisesRegex(ValueError,'un segundo'):
                        await h.append({**event,'request_id':'2'*32},enforce_limits=True)
                    # Reopening the database models reconnects and process restarts.
                    await h.close()
                    h=History(path)
                    await h.open()
                    now.return_value=clock+.999
                    with self.assertRaisesRegex(ValueError,'un segundo'):
                        await h.append({**event,'request_id':'2'*32},enforce_limits=True)
                    await h.append({**event,'client_key':'b'*32,'request_id':'3'*32},enforce_limits=True)
                    now.return_value=clock+1
                    await h.append({**event,'request_id':'2'*32},enforce_limits=True)
                    log={**event,'kind':'log','call':'OA6DXV','request_id':'4'*32}
                    await h.append(log,enforce_limits=True)
                    await h.append({**log,'call':'oa6dxv','client_key':'b'*32,'request_id':'5'*32},enforce_limits=True)
                    await h.append(log,enforce_limits=True)
                    await h.close()
                    h=History(path)
                    await h.open()
                    with self.assertRaisesRegex(ValueError,'dos entradas'):
                        await h.append({**log,'request_id':'6'*32},enforce_limits=True)
                    # A failed write consumes no allowance for other callsigns.
                    await h.append({**log,'call':'CP4NET','request_id':'7'*32},enforce_limits=True)
                    now.return_value=(int(clock)//86400+1)*86400
                    await h.append({**log,'request_id':'8'*32},enforce_limits=True)
                finally:
                    await h.close()

    async def test_existing_logbook_counts_toward_daily_limit_after_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'community.sqlite3'
            h=History(path)
            await h.open()
            event={'kind':'log','time':int(time.time()),'name':'Test','text':'Old entry',
                   'frequency':7100000,'mode':'USB','call':'OA6DXV','client_key':'a'*32,'request_id':'1'*32}
            await h.append(event)
            await h.append({**event,'call':'oa6dxv','request_id':'2'*32})
            await h.close()
            with sqlite3.connect(path) as db:
                db.execute('DROP TABLE posting_limits')
                db.execute('PRAGMA user_version=1')
            h=History(path)
            await h.open()
            try:
                with self.assertRaisesRegex(ValueError,'dos entradas'):
                    await h.append({**event,'request_id':'3'*32},enforce_limits=True)
            finally:
                await h.close()

    async def test_history_pagination_and_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'community.sqlite3'
            h=History(path,max_size_mb=1)
            await h.open()
            try:
                for i in range(211):
                    await h.append({'kind':'chat','time':int(time.time()),'name':'Test','text':str(i),
                        'frequency':7100000,'mode':'LSB','call':'','client_key':'a'*32,'request_id':f'{i:032x}'})
                latest=await h.history('chat')
                self.assertEqual(len(latest['events']),200)
                self.assertTrue(latest['more'])
                older=await h.history('chat',latest['events'][0]['id'])
                self.assertEqual(len(older['events']),11)
                self.assertFalse(older['more'])
                self.assertLess(older['events'][-1]['id'],latest['events'][0]['id'])
                await h.append({'kind':'log','time':int(time.time())-172800,'name':'Test','text':'Preserved',
                    'frequency':7100000,'mode':'LSB','call':'Test','client_key':'b'*32,'request_id':'c'*32})
                self.assertEqual((await h.history('log'))['events'][0]['text'],'Preserved')
                # Backup while the writer connection is still open.
                proc=await asyncio.create_subprocess_exec(sys.executable,'tools/history_backup.py',str(path),str(Path(directory)/'backups'),stdout=asyncio.subprocess.PIPE)
                output,_=await proc.communicate()
                self.assertEqual(proc.returncode,0)
                with sqlite3.connect(output.decode().strip()) as copied:
                    self.assertEqual(copied.execute('PRAGMA integrity_check').fetchone()[0],'ok')
                    self.assertEqual(copied.execute('SELECT COUNT(*) FROM events').fetchone()[0],212)
            finally:
                await h.close()

    async def test_database_size_removes_oldest_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'community.sqlite3'
            h=History(path,max_size_mb=1)
            await h.open()
            try:
                for i in range(2500):
                    await h.append({'kind':'chat','time':int(time.time()),'name':'Test','text':str(i)+'x'*490,
                        'frequency':7100000,'mode':'LSB','call':'','client_key':'a'*32,'request_id':f'{i:032x}'})
                self.assertLessEqual(path.stat().st_size,1024*1024)
                with sqlite3.connect(path) as db:
                    count,minimum,maximum=db.execute('SELECT COUNT(*),MIN(id),MAX(id) FROM events').fetchone()
                self.assertLess(count,2500)
                self.assertGreater(minimum,1)
                self.assertEqual(maximum,2500)
            finally:
                await h.close()

if __name__=='__main__':unittest.main()
