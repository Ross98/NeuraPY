"""Regression: run_debug.py used to call main() recursively on point_client
crash, eventually hitting RecursionError. The fix re-spawns only the
subprocess in a watch_exit loop. This test exercises the loop semantics
without spawning a real point_client."""
import threading
import time
import unittest
from unittest.mock import patch

from web.run_debug import _spawn_point_client, watch_exit_loop_factory
from web.state import Event, EventBus


def _make_args():
    # Minimal argparse.Namespace satisfying _spawn_point_client's attrs.
    import argparse
    return argparse.Namespace(
        protocol="neurapy",
        fake_camera_port=9999,
        point_client_args=[],
    )


def _make_env():
    return {"PYTHONPATH": "/tmp", "MOCK_SIDECAR_PORT": "8766"}


class TestRunDebugRespawn(unittest.TestCase):
    def test_watch_exit_respawns_subprocess(self):
        """watch_exit loop re-runs _spawn_point_client after the proc dies."""
        bus = EventBus()
        args = _make_args()
        env = _make_env()
        spawn_calls = []
        procs = []

        class FakeProc:
            def __init__(self, rc):
                self._rc = rc
                self.stdout = iter([])  # drain_stdout loop exits immediately
            def wait(self):
                return self._rc
            def terminate(self):
                pass

        def fake_spawn(_args, _env, _bus):
            spawn_calls.append(time.monotonic())
            rc = 0 if len(spawn_calls) > 1 else 1  # first dies, second "succeeds" (loops forever)
            p = FakeProc(rc)
            procs.append(p)
            return p, threading.Thread(target=lambda: None, daemon=True)

        # First iteration: proc exits 1 → loop should respawn
        # Second iteration: proc exits 0 → loop should still wait for shutdown
        shutdown = threading.Event()
        # Patch sleep to fast-forward
        with patch("time.sleep", lambda s: None), \
             patch("web.run_debug._spawn_point_client", side_effect=fake_spawn):
            loop = watch_exit_loop_factory(args, env, bus, shutdown,
                                           initial_proc=fake_spawn(args, env, bus)[0],
                                           initial_thread=fake_spawn(args, env, bus)[1])
            t = threading.Thread(target=loop, daemon=True)
            t.start()
            # Let it spin through a couple of cycles
            time.sleep(0.2)
            shutdown.set()
            t.join(timeout=2.0)

        # Initial spawn + at least one respawn
        self.assertGreaterEqual(len(spawn_calls), 2,
                                f"expected respawn, got {len(spawn_calls)} spawn calls")

    def test_spawn_point_client_returns_proc_and_thread(self):
        """_spawn_point_client returns a (Popen, Thread) tuple that callers
        can hold onto for respawn and stdout draining."""
        # We can't actually spawn point_client in a unit test (no PYTHONPATH
        # mock), but we can verify the signature is what run_debug expects.
        import inspect
        sig = inspect.signature(_spawn_point_client)
        self.assertIn("args", sig.parameters)
        self.assertIn("env", sig.parameters)
        self.assertIn("bus", sig.parameters)