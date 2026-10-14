# SPDX-License-Identifier: GPL-3.0-only
"""Bounded public response work and queue regression tests."""
import asyncio
from pathlib import Path
import struct
import sys
import time
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import BINARY_QUEUE_MAX_BYTES, RELIABLE_QUEUE_MAX_BYTES, Gateway


class FakeSocket:
    def __init__(self):
        self.closed = False

    async def close(self, **_kwargs):
        self.closed = True


class ResourceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.gateway = Gateway(SimpleNamespace(
            site_config=Path(__file__).resolve().parents[1] / "site.example.toml", demo=True))
        self.socket = FakeSocket()
        self.client = {
            "queue": asyncio.Queue(maxsize=32), "queue_bytes": 0,
            "reliable": asyncio.Queue(maxsize=64), "reliable_bytes": 0,
            "inflight_bytes": 0,
            "wake": asyncio.Event(), "closing": False, "ws": self.socket,
            "address": "192.0.2.10",
            "protocol_ready": True, "congestion": 0,
            "waterfall_profile": "high", "waterfall_speed": "high",
            "waterfall_zoom": 1.0, "waterfall_center": self.gateway.center_frequency,
            "snapshot_task": None, "snapshot_revision": 0, "snapshot_last_at": 0.0,
            "audio_profile": "raw", "digital_mode": None,
        }
        self.gateway.clients[1] = self.client

    async def asyncTearDown(self):
        task = self.client["snapshot_task"]
        if task:
            task.cancel()
            await task
        worker = getattr(self.gateway.history, "worker", None)
        if worker:
            worker.shutdown(wait=False)

    async def test_binary_queue_is_bounded_by_bytes(self):
        first = bytes(3 * 1024 * 1024)
        second = bytes(2 * 1024 * 1024)
        self.gateway.publish(first, 1)
        self.gateway.publish(second, 1)
        self.assertEqual(self.client["queue_bytes"], len(second))
        self.assertEqual(self.client["queue"].qsize(), 1)
        self.assertIs(self.client["queue"].get_nowait(), second)
        self.assertLessEqual(self.client["queue_bytes"], BINARY_QUEUE_MAX_BYTES)
        self.assertEqual(self.gateway.resource_metrics["binary_queue_peak_bytes"], len(first))
        self.assertGreaterEqual(self.gateway.resource_metrics["output_peak_bytes"], len(first))

    async def test_reliable_queue_closes_before_byte_exhaustion(self):
        first = "a" * (2 * 1024 * 1024)
        second = "b" * (2 * 1024 * 1024)
        self.gateway.publish(first, 1)
        self.gateway.publish(second, 1)
        await asyncio.sleep(0)
        self.assertTrue(self.socket.closed)
        self.assertEqual(self.client["reliable_bytes"], len(first))
        self.assertLessEqual(self.client["reliable_bytes"], RELIABLE_QUEUE_MAX_BYTES)

    async def test_rapid_view_changes_only_encode_latest_revision(self):
        for sequence in range(8):
            self.gateway.waterfall_history.append((sequence, bytes([sequence]) * 4096))
        for revision in (11, 12, 13):
            self.gateway.request_snapshot(1, revision, 4, True)
        await self.client["snapshot_task"]
        self.assertEqual(self.gateway.resource_metrics["snapshot_requests"], 3)
        self.assertEqual(self.gateway.resource_metrics["snapshot_coalesced"], 2)
        snapshots = [packet for packet in list(self.client["queue"]._queue) if packet[0] == 13]
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(struct.unpack_from("<I", snapshots[0], 1)[0], 13)

    async def test_snapshot_work_is_paced_across_shared_address(self):
        for sequence in range(4):
            self.gateway.waterfall_history.append((sequence, bytes([sequence]) * 4096))
        second = {**self.client,
                  "queue": asyncio.Queue(maxsize=32), "queue_bytes": 0,
                  "reliable": asyncio.Queue(maxsize=64), "reliable_bytes": 0,
                  "wake": asyncio.Event(), "snapshot_task": None,
                  "snapshot_revision": 0, "snapshot_last_at": 0.0}
        self.gateway.clients[2] = second
        import server
        with mock.patch("server.SNAPSHOT_DEBOUNCE", 0.01):
            self.gateway.request_snapshot(1, 1, 4, True)
            self.gateway.request_snapshot(2, 1, 4, True)
            await asyncio.gather(self.client["snapshot_task"], second["snapshot_task"])
        self.assertEqual(self.gateway.resource_metrics["snapshot_ip_delays"], 1)
        # The first encode consumes part of the 250 ms slot before the second
        # task observes it; the measured sleep must still be material.
        self.assertGreaterEqual(self.gateway.resource_metrics["snapshot_ip_delay_ms_max"], 100)

    async def test_cancelled_address_reservations_have_bounded_horizon(self):
        for sequence in range(4):
            self.gateway.waterfall_history.append((sequence, bytes([sequence]) * 4096))
        # Model a stale reservation left by a task cancelled while sleeping.
        self.gateway.snapshot_address_next[self.client["address"]] = time.monotonic() + 60
        import server
        started = time.monotonic()
        with mock.patch("server.SNAPSHOT_DEBOUNCE", 0.01):
            self.gateway.request_snapshot(1, 1, 4, True)
            await self.client["snapshot_task"]
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 0.9)
        self.assertLessEqual(
            self.gateway.resource_metrics["snapshot_ip_delay_ms_max"], 500)

    async def test_identical_snapshot_view_reuses_bounded_cache(self):
        for sequence in range(4):
            self.gateway.waterfall_history.append((sequence, bytes([sequence]) * 4096))
        view = (list(self.gateway.waterfall_history), "high", "high", 1.0,
                self.gateway.center_frequency)
        import server
        with mock.patch("server.encode_waterfall", wraps=server.encode_waterfall) as encode:
            await self.gateway.publish_waterfall_history(1, 21, 4, view)
            first_calls = encode.call_count
            await self.gateway.publish_waterfall_history(1, 22, 4, view)
        self.assertGreater(first_calls, 0)
        self.assertEqual(encode.call_count, first_calls)
        self.assertEqual(self.gateway.resource_metrics["snapshot_cache_misses"], 1)
        self.assertEqual(self.gateway.resource_metrics["snapshot_cache_hits"], 1)
        first_rows = self.gateway.resource_metrics["snapshot_rows_encoded"]
        self.assertGreater(first_rows, 0)
        self.assertGreater(self.gateway.resource_metrics["snapshot_bytes_encoded"], 0)
        self.assertEqual(self.gateway.resource_metrics["snapshot_bytes_last"],
                         self.gateway.resource_metrics["snapshot_bytes_max"])
        revisions = [struct.unpack_from("<I", packet, 1)[0]
                     for packet in self.client["queue"]._queue if packet[0] == 13]
        self.assertEqual(revisions, [21, 22])
        self.assertLessEqual(self.gateway.snapshot_cache_bytes, 16 * 1024 * 1024)

        # A newer FFT row must invalidate reuse even before the TTL expires.
        self.gateway.waterfall_history.append((99, bytes([99]) * 4096))
        fresh_view = (list(self.gateway.waterfall_history), "high", "high", 1.0,
                      self.gateway.center_frequency)
        with mock.patch("server.encode_waterfall", wraps=server.encode_waterfall) as fresh_encode:
            await self.gateway.publish_waterfall_history(1, 23, 4, fresh_view)
        self.assertGreater(fresh_encode.call_count, 0)
        self.assertEqual(self.gateway.resource_metrics["snapshot_cache_misses"], 2)
        self.assertGreater(self.gateway.resource_metrics["snapshot_rows_encoded"], first_rows)

    async def test_recent_history_cache_and_shared_read_budget(self):
        class StubHistory:
            calls = 0

            async def history(self, category, before):
                self.calls += 1
                return {"type": "history", "kind": category, "events": [], "more": False, "before": before}

        stub = StubHistory()
        self.gateway.history.worker.shutdown(wait=False)
        self.gateway.history = stub
        first = await self.gateway.history_packet("chat")
        self.assertEqual(first, await self.gateway.history_packet("chat"))
        self.gateway.history_last -= 60
        self.assertEqual(first, await self.gateway.history_packet("chat"))
        self.assertEqual(stub.calls, 1)
        self.assertEqual(self.gateway.resource_metrics["history_cache_misses"], 1)
        self.assertEqual(self.gateway.resource_metrics["history_cache_hits"], 2)
        self.assertEqual([self.gateway.allow_history_read() for _ in range(9)],
                         [True] * 8 + [False])
        self.assertEqual(self.gateway.resource_metrics["history_rate_limited"], 1)

    async def test_audio_queue_drain_preserves_byte_accounting(self):
        waterfall = bytes([1]) + bytes(100)
        audio = bytes([2]) + bytes(100)
        digital = bytes([12]) + bytes(100)
        self.gateway.publish(waterfall, 1)
        self.gateway.publish(audio, 1)
        self.gateway.publish(digital, 1)
        self.gateway.audio_state(1, False)
        self.assertEqual(self.client["queue_bytes"], len(waterfall))
        self.gateway.publish(audio, 1)
        self.gateway.audio_profile(1, "raw")
        self.assertEqual(self.client["queue_bytes"], len(waterfall))
        self.gateway.publish(digital, 1)
        self.gateway.digital_mode(1, None)
        self.assertEqual(self.client["queue_bytes"], len(waterfall))
        self.assertEqual(self.client["queue_bytes"],
                         sum(len(packet) for packet in self.client["queue"]._queue))


if __name__ == "__main__":
    unittest.main()
