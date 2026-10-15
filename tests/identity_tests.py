# SPDX-License-Identifier: GPL-3.0-only
"""Cookie-free identity persistence, admission and resource regressions."""
import asyncio
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import integration_test as radio
from community import History, IdentityError, IDENTITY_TTL
from server import GATEWAY


class RegistryTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_schema_migration_rolls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'community.sqlite3'
            history = History(path)
            await history.open()
            await history.close()
            with sqlite3.connect(path) as db:
                db.execute('DROP TABLE browser_identities')
                db.execute('PRAGMA user_version=2')
            connect = sqlite3.connect
            def fail_index(*args, **kwargs):
                db = connect(*args, **kwargs)
                db.set_authorizer(lambda action, name, *_:
                    sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_CREATE_INDEX and name == 'browser_identity_expiry'
                    else sqlite3.SQLITE_OK)
                return db
            history = History(path)
            try:
                with patch('community.sqlite3.connect', side_effect=fail_index):
                    with self.assertRaises(sqlite3.DatabaseError):
                        await history.open()
            finally:
                await history.close()
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 2)
                self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='browser_identities'").fetchone())

    async def test_persistence_hash_only_expiry_and_revocation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'community.sqlite3'
            history = History(path)
            await history.open()
            with patch('community.time.time', return_value=1800000000):
                identity, token = await history.identity()
                self.assertEqual(len(token), 64)
                self.assertEqual(await history.identity(token), (identity, None))
            await history.close()
            with sqlite3.connect(path) as db:
                row = db.execute('SELECT * FROM browser_identities').fetchone()
                self.assertNotIn(token, row)
                self.assertEqual(row[1], hashlib.sha256(token.encode()).hexdigest())
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 3)
            history = History(path)
            await history.open()
            try:
                with patch('community.time.time', return_value=1800000001):
                    self.assertEqual(await history.identity(token), (identity, None))
                with patch('community.time.time', return_value=1800000002 + IDENTITY_TTL):
                    with self.assertRaises(IdentityError):
                        await history.identity(token)
                    _, fresh = await history.identity()
                def revoke():
                    with history.db:
                        history.db.execute('UPDATE browser_identities SET revoked=1')
                await history.call(revoke)
                with self.assertRaises(IdentityError):
                    await history.identity(fresh)
                for malformed in ('', 'f' * 64, 'a' * 65, [], 123):
                    with self.assertRaises(IdentityError):
                        await history.identity(malformed)
            finally:
                await history.close()

    async def test_capacity_rejects_without_evicting_valid_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            history = History(Path(directory) / 'community.sqlite3')
            await history.open()
            try:
                history.max_size_bytes = 2048  # Independent row-cap calculation.
                identity, token = await history.identity()
                with self.assertRaises(IdentityError) as error:
                    await history.identity()
                self.assertEqual(error.exception.code, 'identity-capacity')
                self.assertEqual(await history.identity(token), (identity, None))
            finally:
                await history.close()


class AdmissionTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = radio.RadioTests.asyncSetUp
    asyncTearDown = radio.RadioTests.asyncTearDown
    start_server = radio.RadioTests.start_server
    connect = radio.RadioTests.connect
    event = radio.RadioTests.event
    identify = radio.RadioTests.identify

    async def raw(self, token=None, **extra):
        ws = await self.session.ws_connect(self.url + '/ws', origin=self.url)
        self.sockets.append(ws)
        await ws.send_json({'type': 'hello', 'protocol': 2, 'waterfall': 2,
                            **({'token': token} if token is not None else {}), **extra})
        return ws

    async def session_action(self, token, action='transfer', origin=None):
        return await self.session.post(self.url + '/session', headers={'Origin':origin or self.url},
                                       json={'action':action, 'token':token})

    async def drain_closed(self, ws):
        async with asyncio.timeout(5):
            while not ws.closed:
                await ws.receive()
        return ws.close_code

    async def test_explicit_transfer_at_capacity_only_closes_matching_identity(self):
        first, other = await self.connect(), await self.connect()
        token = self.credentials[first]
        await self.identify(first, 'OA6DXV')
        gateway = self.app[GATEWAY]
        old_id = next(ident for ident, client in gateway.clients.items() if client['name'] == 'OA6DXV')
        gateway.args.max_clients = 2
        gateway.command = AsyncMock(wraps=gateway.command)
        async with await self.session_action(token, 'status') as response:
            self.assertEqual(await response.json(), {'active':True})
        closed = asyncio.create_task(self.drain_closed(first))
        async with await self.session_action(token) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(await response.json(), {'transferred':True})
        self.assertEqual(await closed, 4410)
        self.assertNotIn(old_id, gateway.clients)
        self.assertIn(f'del {old_id}', [call.args[0] for call in gateway.command.call_args_list])
        self.assertFalse(other.closed)
        self.assertEqual(gateway.info()['users'], 1)
        await other.send_json({'type':'tune', 'frequency':7101000})
        self.assertEqual((await self.event(other, 'tuned'))['frequency'], 7101000)
        restored = await self.connect(token)
        await self.identify(restored, 'OA6DXV')
        self.assertEqual(gateway.info()['users'], 2)

    async def test_transfer_rejects_forgery_origin_and_browser_chosen_keys(self):
        first = await self.connect()
        token = self.credentials[first]
        for bad_token, status in (('f'*64,401),('a'*32,400),(None,400)):
            async with await self.session_action(bad_token) as response:
                self.assertEqual(response.status, status)
        async with await self.session_action(token, origin='https://other.example') as response:
            self.assertEqual(response.status, 403)
        self.assertFalse(first.closed)
        self.assertEqual(self.app[GATEWAY].info()['users'], 1)

    async def test_concurrent_transfers_are_serialized_and_rate_limited(self):
        first = await self.connect()
        token = self.credentials[first]
        closed = asyncio.create_task(self.drain_closed(first))
        responses = await asyncio.gather(self.session_action(token), self.session_action(token))
        try:
            self.assertEqual(sorted(response.status for response in responses), [200,429])
            for response in responses:
                self.assertNotIn(token, await response.text())
        finally:
            for response in responses:
                response.release()
        self.assertEqual(await closed, 4410)
        self.assertFalse(self.app[GATEWAY].identity_owners)
        self.assertEqual(self.app[GATEWAY].session_requests, 0)

    async def test_no_receiver_or_av_before_admission_and_legacy_rejected(self):
        gateway = self.app[GATEWAY]
        gateway.command = AsyncMock(wraps=gateway.command)
        ws = await self.session.ws_connect(self.url + '/ws', origin=self.url)
        self.sockets.append(ws)
        await asyncio.sleep(.25)
        self.assertFalse(any(call.args[0].startswith('set ') for call in gateway.command.call_args_list))
        self.assertEqual(gateway.info()['users'], 0)
        with self.assertRaises(asyncio.TimeoutError):
            await ws.receive(timeout=.1)
        await ws.send_json({'type': 'hello', 'protocol': 1, 'waterfall': 2})
        await ws.receive()
        self.assertEqual(ws.close_code, 1002)
        self.assertFalse(gateway.identity_owners)

    async def test_duplicate_token_atomic_admission_explicit_retry_and_private_output(self):
        first = await self.connect()
        token = self.credentials[first]
        gateway = self.app[GATEWAY]
        gateway.command = AsyncMock(wraps=gateway.command)
        second, third = await asyncio.gather(self.raw(token), self.raw(token))
        errors = await asyncio.gather(self.event(second, 'session-error'), self.event(third, 'session-error'))
        self.assertTrue(all(error['code'] == 'identity-active' for error in errors))
        self.assertFalse(any(call.args[0].startswith('set ') for call in gateway.command.call_args_list))
        self.assertEqual(gateway.info()['users'], 1)
        self.assertNotIn(token, json.dumps(gateway.info()))
        await first.close()
        restored = await self.connect(token)
        await self.identify(restored, 'OA6DXV')
        presence = await self.event(restored, 'presence')
        self.assertNotIn(token, json.dumps(presence))
        self.assertEqual(gateway.info()['users'], 1)
        self.assertEqual(sum(map(len, gateway.identity_owners.values())), 1)

    async def test_same_ip_distinct_identities_and_configurable_limit(self):
        first, second = await self.connect(), await self.connect()
        self.assertNotEqual(self.credentials[first], self.credentials[second])
        gateway = self.app[GATEWAY]
        gateway.args.max_connections_per_identity = 2
        third = await self.connect(self.credentials[first])
        await self.identify(third, 'OA6DXV')
        self.assertEqual(gateway.info()['users'], 3)

    async def test_unknown_and_malformed_credentials_do_not_issue_or_allocate(self):
        gateway = self.app[GATEWAY]
        gateway.command = AsyncMock(wraps=gateway.command)
        for token in ('f' * 64, '', 'bad'):
            ws = await self.raw(token)
            error = await self.event(ws, 'session-error')
            self.assertEqual(error['code'], 'identity-invalid')
            await ws.close()
        self.assertEqual(await gateway.history.call(lambda: gateway.history.db.execute(
            'SELECT COUNT(*) FROM browser_identities').fetchone()[0]), 0)
        self.assertFalse(any(call.args[0].startswith('set ') for call in gateway.command.call_args_list))

    async def test_database_failure_is_closed_and_pending_controls_cannot_allocate(self):
        gateway = self.app[GATEWAY]
        gateway.command = AsyncMock(wraps=gateway.command)
        ws = await self.session.ws_connect(self.url + '/ws', origin=self.url)
        self.sockets.append(ws)
        await ws.send_json({'type': 'audio', 'enabled': True})
        await ws.receive()
        self.assertEqual(ws.close_code, 1002)
        with self.assertLogs(level='ERROR'), patch.object(gateway.history, 'identity', side_effect=sqlite3.OperationalError('test unavailable')):
            ws = await self.raw()
            error = await self.event(ws, 'session-error')
            self.assertEqual(error['code'], 'identity-unavailable')
        self.assertFalse(any(call.args[0].startswith('set ') for call in gateway.command.call_args_list))

    async def test_rename_and_reconnect_preserve_cooldown_and_retry_identity(self):
        ws = await self.connect()
        token = self.credentials[ws]
        await self.identify(ws, 'OA6DXV')
        request = {'type': 'chat', 'request_id': '1' * 32, 'text': 'Stored once'}
        await ws.send_json(request)
        ack = await self.event(ws, 'ack')
        await self.identify(ws, 'N0CALL')
        await ws.send_json({**request, 'request_id': '2' * 32})
        self.assertIn('un segundo', (await self.event(ws, 'error'))['message'])
        await ws.close()
        ws = await self.connect(token)
        await self.identify(ws, 'N0CALL')
        await ws.send_json(request)
        retry = await self.event(ws, 'ack')
        self.assertEqual(retry['event']['id'], ack['event']['id'])
        self.assertEqual(retry['event']['name'], 'OA6DXV')
        await ws.send_json({'type': 'identify', 'name': 'OTHER', 'key': 'a' * 32})
        await self.event(ws, 'error')

    async def test_issuance_bucket_and_pending_connection_capacity(self):
        gateway = self.app[GATEWAY]
        for _ in range(6):
            gateway.check_identity_issuance('192.0.2.10')
        with self.assertRaises(IdentityError):
            gateway.check_identity_issuance('192.0.2.10')
        for _ in range(self.args.max_clients):
            ws = await self.session.ws_connect(self.url + '/ws', origin=self.url)
            self.sockets.append(ws)
        with self.assertRaises(radio.WSServerHandshakeError) as error:
            await self.session.ws_connect(self.url + '/ws', origin=self.url)
        self.assertEqual(error.exception.status, 503)
        self.assertEqual(gateway.info()['users'], 0)


if __name__ == '__main__':
    unittest.main()
