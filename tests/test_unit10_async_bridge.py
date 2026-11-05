"""Unit 10 async bridge: exception propagation and clean shutdown."""

import asyncio
import threading

import pytest

from agentic_dev_loop.engine.runtimes.async_bridge import AsyncBridge
from agentic_dev_loop.engine.runtimes.errors import HarnessTimeoutError


def test_returns_the_coroutine_result():
    assert AsyncBridge().run(lambda: _value(7)) == 7


async def _value(value):
    await asyncio.sleep(0)
    return value


def test_reraises_the_coroutine_exception_on_the_caller_thread():
    def boom():
        raise ValueError("harness exploded")

    with pytest.raises(ValueError, match="harness exploded"):
        AsyncBridge().run(lambda: _raise(boom))


async def _raise(fn):
    fn()


def test_close_joins_the_worker_thread():
    bridge = AsyncBridge()
    bridge.run(lambda: _value("done"))
    bridge.close()
    assert all(not thread.name.startswith("agent-runtime-async")
               for thread in threading.enumerate())


def test_bounded_wait_times_out():
    with pytest.raises(HarnessTimeoutError):
        AsyncBridge().run(lambda: _slow(), timeout=0.01)


async def _slow():
    await asyncio.sleep(5)
    return None
