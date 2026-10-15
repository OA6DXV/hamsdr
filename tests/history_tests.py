# SPDX-License-Identifier: GPL-3.0-only
"""Cursor pagination, durable backup, size limiting and retry uniqueness."""
import asyncio
from contextlib import closing
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
    @staticmethod
    def event(request='1', kind='chat'):
        return {'kind':kind,'time':int(time.time()),'name':'Test','text':'Preserved',
                'frequency':7100000,'mode':'USB','call':'OA6DXV' if kind=='log' else '',
                'client_key':'a'*32,'request_id':request*32}

    async def legacy_fixture(self, path):
        history = History(path)
        await history.open()
        identity, token = await history.identity()
        await history.append(self.event('1'))
        await history.append(self.event('2', 'log'), enforce_limits=True)
        await history.append(self.event('3'))
        await history.call(history.events_db.execute, 'DELETE FROM events WHERE id=3')
        await history.call(history.events_db.commit)
        await history.close()
        with closing(sqlite3.connect(history.history_path)) as records, records:
            schema = records.execute("SELECT sql FROM sqlite_master WHERE name='events'").fetchone()[0]
            rows = records.execute('SELECT * FROM events').fetchall()
        with closing(sqlite3.connect(path)) as state, state:
            state.execute(schema)
            state.executemany('INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?)', rows)
            state.execute("UPDATE sqlite_sequence SET seq=3 WHERE name='events'")
            state.execute('DROP TABLE pending_publication')
            state.execute('PRAGMA user_version=3')
        history.history_path.unlink()
        return identity, token

    async def test_separate_databases_migrate_without_changing_ids_or_tokens(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'community.sqlite3'
            identity, token = await self.legacy_fixture(path)
            history = History(path)
            await history.open()
            try:
                self.assertEqual(await history.identity(token), (identity, None))
                self.assertEqual((await history.history('chat'))['events'][0]['id'], 1)
                self.assertEqual((await history.history('log'))['events'][0]['id'], 2)
                saved, _ = await history.append(self.event('4'))
                self.assertEqual(saved['id'], 4, 'Deleted IDs must not be reused')
                with closing(sqlite3.connect(path)) as db, db:
                    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    self.assertNotIn('events', tables)
                    self.assertTrue({'posting_limits','browser_identities','pending_publication'} <= tables)
                with closing(sqlite3.connect(history.history_path)) as db, db:
                    self.assertEqual({r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")},
                                     {'events','sqlite_sequence'})
            finally:
                await history.close()
            history = History(path)
            await history.open()
            try:
                self.assertEqual(len((await history.history('chat'))['events']), 2)
                self.assertEqual(await history.identity(token), (identity, None))
            finally:
                await history.close()

    async def test_migration_retries_committed_copy_after_interruption(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'community.sqlite3'
            await self.legacy_fixture(path)
            connect = sqlite3.connect
            def fail_drop(database, *args, **kwargs):
                db = connect(database, *args, **kwargs)
                if Path(database) == path:
                    db.set_authorizer(lambda action, name, *_:
                        sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_DROP_TABLE and name == 'events'
                        else sqlite3.SQLITE_OK)
                return db
            history = History(path)
            with patch('community.sqlite3.connect', side_effect=fail_drop):
                with self.assertRaises(sqlite3.DatabaseError):
                    await history.open()
            with closing(sqlite3.connect(path)) as old, old:
                self.assertEqual(old.execute('SELECT COUNT(*) FROM events').fetchone()[0], 2)
            with closing(sqlite3.connect(history.history_path)) as copied, copied:
                self.assertEqual(copied.execute('SELECT COUNT(*) FROM events').fetchone()[0], 2)
            history = History(path)
            await history.open()
            try:
                self.assertEqual(len((await history.history('chat'))['events']), 1)
                self.assertEqual(len((await history.history('log'))['events']), 1)
            finally:
                await history.close()

    async def test_migration_conflict_keeps_original_history(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'community.sqlite3'
            await self.legacy_fixture(path)
            history = History(path)
            with closing(sqlite3.connect(history.history_path)) as db, db:
                db.execute('CREATE TABLE events(id INTEGER PRIMARY KEY,kind,time,name,text,frequency,mode,call,client_key,request_id)')
                db.execute("INSERT INTO events VALUES(1,'chat',0,'Other','Conflict',0,'USB','','other','other')")
            with self.assertRaisesRegex(RuntimeError, 'conflict'):
                await history.open()
            with closing(sqlite3.connect(path)) as db, db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM events').fetchone()[0], 2)
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 3)

    async def test_interrupted_reservation_without_event_restores_previous_log_quota(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'community.sqlite3'
            history = History(path)
            await history.open()
            event = self.event('1', 'log')
            await history.append(event, enforce_limits=True)
            await history.call(history._reserve_publication, {**event,'request_id':'2'*32})
            await history.close()
            history = History(path)
            await history.open()
            try:
                await history.append({**event,'request_id':'2'*32}, enforce_limits=True)
                with self.assertRaisesRegex(ValueError, 'dos entradas'):
                    await history.append({**event,'request_id':'3'*32}, enforce_limits=True)
                self.assertEqual(len((await history.history('log'))['events']), 2)
            finally:
                await history.close()

    async def test_interrupted_first_reservation_does_not_consume_cooldown(self):
        with tempfile.TemporaryDirectory() as directory, patch('community.time.time', return_value=1800000000):
            path = Path(directory)/'community.sqlite3'
            history = History(path)
            await history.open()
            event = self.event()
            await history.call(history._reserve_publication, event)
            await history.close()
            history = History(path)
            await history.open()
            try:
                _, created = await history.append(event, enforce_limits=True)
                self.assertTrue(created)
            finally:
                await history.close()

    async def test_interrupted_finalization_preserves_event_quota_and_retry_id(self):
        with tempfile.TemporaryDirectory() as directory, patch('community.time.time', return_value=1800000000):
            path = Path(directory)/'community.sqlite3'
            history = History(path)
            await history.open()
            event = self.event()
            with patch.object(history, '_recover_pending', side_effect=[None, RuntimeError('Interrupted')]):
                with self.assertRaisesRegex(RuntimeError, 'Interrupted'):
                    await history.append(event, enforce_limits=True)
            await history.close()
            history = History(path)
            await history.open()
            try:
                saved, created = await history.append(event, enforce_limits=True)
                self.assertFalse(created)
                self.assertEqual(saved['id'], 1)
                with self.assertRaisesRegex(ValueError, 'un segundo'):
                    await history.append({**event,'request_id':'2'*32}, enforce_limits=True)
            finally:
                await history.close()

    async def test_failed_history_insert_refunds_reservation(self):
        with tempfile.TemporaryDirectory() as directory:
            history = History(Path(directory)/'community.sqlite3')
            await history.open()
            try:
                event = self.event('1', 'log')
                await history.append(event, enforce_limits=True)
                def deny_insert():
                    history.events_db.set_authorizer(lambda action, name, *_:
                        sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_INSERT and name == 'events' else sqlite3.SQLITE_OK)
                await history.call(deny_insert)
                with self.assertRaises(sqlite3.DatabaseError):
                    await history.append({**event,'request_id':'2'*32}, enforce_limits=True)
                await history.call(history.events_db.set_authorizer, None)
                await history.append({**event,'request_id':'2'*32}, enforce_limits=True)
                with self.assertRaisesRegex(ValueError, 'dos entradas'):
                    await history.append({**event,'request_id':'3'*32}, enforce_limits=True)
            finally:
                await history.close()

    def test_cannot_use_one_database_for_both_roles(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'community.sqlite3'
            with self.assertRaisesRegex(ValueError, 'different files'):
                History(path, history_path=path)

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
            with closing(sqlite3.connect(path)) as db, db:
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
                proc=await asyncio.create_subprocess_exec(sys.executable,'tools/history_backup.py',str(h.history_path),str(Path(directory)/'backups'),stdout=asyncio.subprocess.PIPE)
                output,_=await proc.communicate()
                self.assertEqual(proc.returncode,0)
                with closing(sqlite3.connect(output.decode().strip())) as copied, copied:
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
                self.assertLessEqual(h.history_path.stat().st_size,1024*1024)
                with closing(sqlite3.connect(h.history_path)) as db, db:
                    count,minimum,maximum=db.execute('SELECT COUNT(*),MIN(id),MAX(id) FROM events').fetchone()
                self.assertLess(count,2500)
                self.assertGreater(minimum,1)
                self.assertEqual(maximum,2500)
            finally:
                await h.close()

if __name__=='__main__':unittest.main()
