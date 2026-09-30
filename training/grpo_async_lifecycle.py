"""Synchronous adapter for an explicitly supplied async observation lifecycle."""

from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
import threading
from collections.abc import Mapping
from typing import Any


_CALLBACK_NAMES = ("begin", "before_action", "execute", "finish")


class ManagedAsyncObservationLifecycle:
    """Run async observation callbacks on one managed loop for sync GRPO calls.

    Instances are one-shot context managers. Constructing one validates its
    injected callbacks but starts no thread and performs no I/O.
    """

    def __init__(self, async_lifecycle: Any) -> None:
        callbacks = {}
        for name in _CALLBACK_NAMES:
            callback = getattr(async_lifecycle, name, None)
            if not callable(callback):
                raise TypeError(f"Async observation lifecycle requires callable {name}")
            callbacks[name] = callback

        self._callbacks = callbacks
        self._state_lock = threading.RLock()
        self._call_lock = threading.Lock()
        self._close_lock = threading.Lock()
        self._ready = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._loop_thread_ident: int | None = None
        self._active_future: concurrent.futures.Future[Any] | None = None
        self._thread_error: BaseException | None = None
        self._shutdown_error: BaseException | None = None
        self._ever_entered = False
        self._entered = False
        self._closing = False
        self._closed = False

    def __enter__(self) -> ManagedAsyncObservationLifecycle:
        with self._state_lock:
            if self._closed:
                raise RuntimeError("Async observation lifecycle is closed")
            if self._ever_entered:
                raise RuntimeError("Async observation lifecycle was already entered")
            self._ever_entered = True
            loop = asyncio.new_event_loop()
            thread = threading.Thread(
                target=self._run_loop,
                args=(loop,),
                name="atlasops-observation-lifecycle",
                daemon=False,
            )
            self._loop = loop
            self._thread = thread
            self._entered = True
            try:
                thread.start()
            except BaseException:
                self._entered = False
                self._closed = True
                loop.close()
                raise

        self._ready.wait()
        if self._thread_error is not None:
            thread.join()
            with self._state_lock:
                self._entered = False
                self._closed = True
            raise RuntimeError("Async observation lifecycle loop failed to start") from (
                self._thread_error
            )
        return self

    def __exit__(self, exc_type: Any, exc: BaseException | None, tb: Any) -> bool:
        try:
            self.close()
        except BaseException as close_error:
            if exc is None:
                raise
            exc.add_note(
                "Async observation lifecycle shutdown also failed: "
                f"{type(close_error).__name__}"
            )
        return False

    def begin(self, scenario_id: str) -> Mapping[str, Any]:
        return self._invoke("begin", scenario_id)

    def before_action(
        self, snapshot: Mapping[str, Any], index: int
    ) -> Mapping[str, Any]:
        return self._invoke("before_action", snapshot, index)

    def execute(
        self, raw_completion: str, state: Mapping[str, Any], index: int
    ) -> Mapping[str, Any]:
        return self._invoke("execute", raw_completion, state, index)

    def finish(self, group: Mapping[str, Any], status: str) -> None:
        self._invoke("finish", group, status)

    def _invoke(self, name: str, *args: Any) -> Any:
        if threading.get_ident() == self._loop_thread_ident:
            raise RuntimeError(
                "Synchronous lifecycle invocation from its owned loop thread would deadlock"
            )
        if not self._call_lock.acquire(blocking=False):
            raise RuntimeError("Overlapping synchronous lifecycle calls are not allowed")

        future: concurrent.futures.Future[Any] | None = None
        try:
            with self._state_lock:
                if not self._entered or self._closed or self._closing:
                    raise RuntimeError("Async observation lifecycle is not entered or is closed")
                loop = self._loop
                thread = self._thread
                if (
                    loop is None
                    or thread is None
                    or not thread.is_alive()
                    or loop.is_closed()
                ):
                    raise RuntimeError("Async observation lifecycle loop is not running")
                callback = self._callbacks[name]
                coroutine = self._await_callback(callback, args)
                try:
                    future = asyncio.run_coroutine_threadsafe(coroutine, loop)
                except BaseException:
                    coroutine.close()
                    raise
                self._active_future = future

            try:
                return future.result()
            except concurrent.futures.CancelledError as exc:
                if future.cancelled():
                    raise asyncio.CancelledError() from exc
                if not future.done():
                    self._wait_for_callback_settlement(future, exc)
                raise
            except BaseException as exc:
                if not future.done():
                    self._wait_for_callback_settlement(future, exc)
                raise
        finally:
            with self._state_lock:
                if self._active_future is future:
                    self._active_future = None
            self._call_lock.release()

    @staticmethod
    async def _await_callback(callback: Any, args: tuple[Any, ...]) -> Any:
        result = callback(*args)
        if not inspect.isawaitable(result):
            raise TypeError("Async observation lifecycle callback must return an awaitable")
        return await result

    @staticmethod
    def _wait_for_callback_settlement(
        future: concurrent.futures.Future[Any], interruption: BaseException
    ) -> None:
        while True:
            try:
                future.result()
            except BaseException as callback_error:
                if not future.done():
                    if callback_error is not interruption:
                        interruption.add_note(
                            "Synchronous wait was interrupted again before the "
                            "async lifecycle callback settled"
                        )
                    continue
                if callback_error is not interruption:
                    interruption.add_note(
                        "Async lifecycle callback also settled with "
                        f"{type(callback_error).__name__}"
                    )
                return
            return

    def close(self) -> None:
        if threading.get_ident() == self._loop_thread_ident:
            raise RuntimeError("Cannot close async observation lifecycle from its owned loop thread")

        with self._close_lock:
            with self._state_lock:
                if self._closed:
                    return
                if not self._ever_entered:
                    self._closed = True
                    return
                self._closing = True
                loop = self._loop
                thread = self._thread
                future = self._active_future

            if future is not None:
                future.cancel()
            if loop is not None and not loop.is_closed():
                try:
                    loop.call_soon_threadsafe(loop.stop)
                except RuntimeError as exc:
                    self._shutdown_error = exc
            if thread is not None:
                thread.join()

            with self._state_lock:
                self._closed = True
                self._closing = False
                error = self._thread_error or self._shutdown_error
            if error is not None:
                raise RuntimeError("Async observation lifecycle loop shutdown failed") from error

    def _run_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        try:
            asyncio.set_event_loop(loop)
            self._loop_thread_ident = threading.get_ident()
            self._ready.set()
            loop.run_forever()
        except BaseException as exc:
            self._thread_error = exc
            self._ready.set()
        finally:
            try:
                self._drain_pending_tasks(loop)
            except BaseException as exc:
                self._shutdown_error = self._shutdown_error or exc
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except BaseException as exc:
                self._shutdown_error = self._shutdown_error or exc
            try:
                shutdown_executor = getattr(loop, "shutdown_default_executor", None)
                if callable(shutdown_executor):
                    loop.run_until_complete(shutdown_executor())
            except BaseException as exc:
                self._shutdown_error = self._shutdown_error or exc
            try:
                loop.close()
            except BaseException as exc:
                self._shutdown_error = self._shutdown_error or exc
            asyncio.set_event_loop(None)

    @staticmethod
    def _drain_pending_tasks(loop: asyncio.AbstractEventLoop) -> None:
        async def drain() -> None:
            while True:
                current = asyncio.current_task(loop)
                pending = [
                    task
                    for task in asyncio.all_tasks(loop)
                    if task is not current and not task.done()
                ]
                if not pending:
                    return
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)

        loop.run_until_complete(drain())
