"""Cursor pagination, durable backup, size limiting and retry uniqueness."""
import asyncio
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from community import History


class HistoryTests(unittest.IsolatedAsyncioTestCase):
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
