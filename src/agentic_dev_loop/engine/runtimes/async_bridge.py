"""Stdlib async-to-sync bridge (unit 10).

The engine's step runner is synchronous; the subprocess harness SDKs are
async. One worker thread per ``run()`` executes the coroutine, then the
caller side re-raises any exception and returns the value. ``close()`` joins
the worker so no thread leaks across attempts.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Coroutine
from typing import Any


class AsyncBridge:
    """Runs one coroutine at a time on a dedicated worker thread."""

    def __init__(self, name: str = "agent-runtime-async") -> None:
        self._name = name
        self._thread: threading.Thread | None = None
        self._result: Any = None
        self._exception: BaseException | None = None

    def run(self, factory: Callable[[], Coroutine[Any, Any, Any]],
            timeout: float | None = None) -> Any:
        """Run ``factory()`` to completion and return its result.

        Exceptions raised inside the coroutine are re-raised on the caller's
        thread; the worker is always joined before returning."""
        self.close()
        self._result = None
        self._exception = None

        def target() -> None:
            try:
                self._result = asyncio.run(factory())
            except BaseException as exc:  # propagated to the caller
                self._exception = exc

        thread = threading.Thread(target=target, name=self._name, daemon=True)
        self._thread = thread
        thread.start()
        thread.join(timeout)
        if thread.is_alive():
            from .errors import HarnessTimeoutError

            raise HarnessTimeoutError(
                f"async runtime did not finish within {timeout}s")
        self._thread = None
        if self._exception is not None:
            exc, self._exception = self._exception, None
            raise exc
        result, self._result = self._result, None
        return result

    def close(self) -> None:
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join()
