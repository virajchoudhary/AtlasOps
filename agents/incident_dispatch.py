"""Serialize queued alert occurrences through the incident handler."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Literal

from agents.correlator import IncidentCorrelator, correlator

log = logging.getLogger(__name__)

DispatchState = Literal["started", "queued", "duplicate"]
OccurrenceState = Literal["queued", "attempted"]
IncidentHandler = Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class DispatchSubmission:
    state: DispatchState
    completion: asyncio.Future[Any] | None = None

    async def wait(self) -> Any:
        if self.completion is None:
            return None
        return await asyncio.shield(self.completion)


@dataclass(frozen=True)
class _PendingDispatch:
    incident_id: str
    occurrence_keys: tuple[str, ...]
    payload: dict[str, Any]
    handler: IncidentHandler
    completion: asyncio.Future[Any]


@dataclass
class _IncidentQueue:
    pending: deque[_PendingDispatch] = field(default_factory=deque)
    deferred: deque[_PendingDispatch] = field(default_factory=deque)
    occurrence_states: dict[str, OccurrenceState] = field(default_factory=dict)
    worker: asyncio.Task[None] | None = None


class IncidentDispatcher:
    def __init__(self, incident_correlator: IncidentCorrelator):
        self._correlator = incident_correlator
        self._lock = RLock()
        self._queues: dict[str, _IncidentQueue] = {}

    def submit(
        self,
        incident_id: str,
        occurrence_keys: tuple[str, ...],
        payload: dict[str, Any],
        handler: IncidentHandler,
    ) -> DispatchSubmission:
        """Admit each claimed occurrence without yielding between correlation and queueing."""
        keys = tuple(dict.fromkeys(occurrence_keys))
        if not keys:
            return DispatchSubmission("duplicate")

        payload_snapshot = deepcopy(payload)
        loop = asyncio.get_running_loop()
        completion = loop.create_future()
        completion.add_done_callback(self._consume_completion_exception)

        with self._lock:
            incident_queue = self._queues.get(incident_id)
            if incident_queue is None:
                incident_queue = _IncidentQueue()
                self._queues[incident_id] = incident_queue

            new_keys = tuple(
                key for key in keys if key not in incident_queue.occurrence_states
            )
            if not new_keys:
                return DispatchSubmission("duplicate")

            incident_queue.occurrence_states.update(
                {key: "queued" for key in new_keys}
            )
            dispatch = _PendingDispatch(
                incident_id=incident_id,
                occurrence_keys=new_keys,
                payload=payload_snapshot,
                handler=handler,
                completion=completion,
            )
            worker = incident_queue.worker
            if worker is not None and (worker.done() or worker.cancelling()):
                incident_queue.deferred.append(dispatch)
                return DispatchSubmission("queued", completion)

            incident_queue.pending.append(dispatch)
            if worker is not None:
                return DispatchSubmission("queued", completion)

            self._correlator.mark_processing(incident_id, True)
            try:
                self._start_worker(incident_id, incident_queue, loop)
            except Exception:
                incident_queue.pending.pop()
                for key in new_keys:
                    incident_queue.occurrence_states.pop(key, None)
                self._queues.pop(incident_id, None)
                self._correlator.rollback_unadmitted_claims(incident_id, new_keys)
                if not completion.done():
                    completion.cancel()
                raise
            return DispatchSubmission("started", completion)

    def _start_worker(
        self,
        incident_id: str,
        incident_queue: _IncidentQueue,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        worker_coroutine = self._drain(incident_id, incident_queue)
        try:
            worker = loop.create_task(
                worker_coroutine,
                name=f"incident-dispatch-{incident_id}",
            )
        except Exception:
            worker_coroutine.close()
            raise
        incident_queue.worker = worker
        # A task cancelled before its first step skips _drain's finally block.
        worker.add_done_callback(
            lambda completed: self._finish_worker(
                incident_id,
                incident_queue,
                completed,
            )
        )

    async def _drain(self, incident_id: str, incident_queue: _IncidentQueue) -> None:
        pending: _PendingDispatch | None = None
        try:
            while True:
                with self._lock:
                    if self._queues.get(incident_id) is not incident_queue:
                        return
                    if not incident_queue.pending:
                        return
                    pending = incident_queue.pending.popleft()
                    for key in pending.occurrence_keys:
                        incident_queue.occurrence_states[key] = "attempted"

                try:
                    result = await pending.handler(
                        pending.payload,
                        incident_id=incident_id,
                    )
                except asyncio.CancelledError:
                    if not pending.completion.done():
                        pending.completion.cancel()
                    raise
                except Exception as exc:
                    log.exception(
                        "incident handler failed for %s; claimed occurrences will not be retried",
                        incident_id,
                    )
                    if not pending.completion.done():
                        pending.completion.set_exception(exc)
                else:
                    if not pending.completion.done():
                        pending.completion.set_result(result)
                pending = None
        finally:
            with self._lock:
                if pending is not None and not pending.completion.done():
                    pending.completion.cancel()
            worker = asyncio.current_task()
            if worker is not None:
                self._finish_worker(incident_id, incident_queue, worker)

    def _finish_worker(
        self,
        incident_id: str,
        incident_queue: _IncidentQueue,
        worker: asyncio.Task[None],
    ) -> None:
        with self._lock:
            if incident_queue.worker is not worker:
                return
            if incident_queue.deferred and not worker.done():
                return
            for abandoned in incident_queue.pending:
                if not abandoned.completion.done():
                    abandoned.completion.cancel()
            incident_queue.pending.clear()
            incident_queue.worker = None
            if self._queues.get(incident_id) is not incident_queue:
                return

            if incident_queue.deferred:
                incident_queue.pending.extend(incident_queue.deferred)
                incident_queue.deferred.clear()
                try:
                    self._start_worker(
                        incident_id,
                        incident_queue,
                        worker.get_loop(),
                    )
                except Exception as exc:
                    failed = tuple(incident_queue.pending)
                    incident_queue.pending.clear()
                    failed_keys = tuple(
                        key for dispatch in failed for key in dispatch.occurrence_keys
                    )
                    for key in failed_keys:
                        incident_queue.occurrence_states.pop(key, None)
                    self._queues.pop(incident_id, None)
                    self._correlator.rollback_unadmitted_claims(
                        incident_id,
                        failed_keys,
                    )
                    self._correlator.mark_processing(incident_id, False)
                    for dispatch in failed:
                        if not dispatch.completion.done():
                            dispatch.completion.set_exception(exc)
                    log.exception(
                        "failed to start deferred incident dispatch for %s",
                        incident_id,
                    )
                return

            self._queues.pop(incident_id, None)
            self._correlator.mark_processing(incident_id, False)

    @staticmethod
    def _consume_completion_exception(completion: asyncio.Future[Any]) -> None:
        if not completion.cancelled():
            completion.exception()


incident_dispatcher = IncidentDispatcher(correlator)
