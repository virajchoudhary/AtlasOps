"""Serialize queued alert occurrences through the incident handler."""

from __future__ import annotations

import asyncio
import logging
import os
from collections import deque
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass, field
from os import PathLike
from pathlib import Path
from threading import RLock
from typing import Any, Literal

from agents.correlator import CorrelatorCandidateBatch, IncidentCorrelator, correlator
from agents.incident_admission import IncidentAdmissionStore

log = logging.getLogger(__name__)

DispatchState = Literal["started", "queued", "duplicate"]
IncidentHandler = Callable[..., Awaitable[Any]]
_ADMISSION_DATABASE_NAME = "incident-admissions.sqlite3"


class _WorkerTaskCreationError(RuntimeError):
    pass


@dataclass(frozen=True)
class DispatchSubmission:
    state: DispatchState
    incident_id: str | None = None
    completion: asyncio.Future[Any] | None = None

    async def wait(self) -> Any:
        if self.completion is None:
            return None
        return await asyncio.shield(self.completion)


@dataclass(frozen=True)
class _PendingDispatch:
    incident_id: str
    admission_token: str
    occurrence_keys: tuple[str, ...]
    payload: dict[str, Any]
    incident_anchors: dict[str, Any] | None
    handler: IncidentHandler
    completion: asyncio.Future[Any]


@dataclass
class _IncidentQueue:
    pending: deque[_PendingDispatch] = field(default_factory=deque)
    deferred: deque[_PendingDispatch] = field(default_factory=deque)
    worker: asyncio.Task[None] | None = None


class IncidentDispatcher:
    def __init__(
        self,
        incident_correlator: IncidentCorrelator,
        database_path: str | PathLike[str] | None = None,
    ):
        if database_path is not None and str(database_path) in {"", ":memory:"}:
            raise ValueError("incident admission requires an on-disk SQLite path")
        self._correlator = incident_correlator
        self._database_path = Path(database_path) if database_path is not None else None
        self._admission_store: IncidentAdmissionStore | None = None
        self._lock = RLock()
        self._queues: dict[str, _IncidentQueue] = {}

    def submit(
        self,
        candidate_batch: CorrelatorCandidateBatch,
        handler: IncidentHandler,
        *,
        incident_anchors: dict[str, Any] | None = None,
    ) -> DispatchSubmission:
        """Durably admit new occurrences before placing them on the in-process queue."""
        keys = tuple(dict.fromkeys(ref.occurrence_key for ref in candidate_batch.occurrence_refs))
        if not keys:
            return DispatchSubmission("duplicate")

        loop = asyncio.get_running_loop()
        completion = loop.create_future()
        completion.add_done_callback(self._consume_completion_exception)

        with self._lock:
            admission_store = self._get_admission_store()
            try:
                admission = admission_store.admit(candidate_batch.incident_id, keys)
            except Exception:
                if not completion.done():
                    completion.cancel()
                raise
            if not admission.admitted_keys:
                persisted_ids = {incident_id for _, incident_id in admission.duplicates}
                persisted_incident_id = next(iter(persisted_ids)) if len(persisted_ids) == 1 else None
                return DispatchSubmission(
                    "duplicate",
                    incident_id=persisted_incident_id,
                )

            if admission.admission_token is None:
                raise RuntimeError("admitted occurrences have no admission token")
            admitted_keys = set(admission.admitted_keys)
            payload_snapshot = self._filtered_payload(candidate_batch, admitted_keys)
            incident_id = candidate_batch.incident_id
            incident_queue = self._queues.get(incident_id)
            if incident_queue is None:
                incident_queue = _IncidentQueue()
                self._queues[incident_id] = incident_queue

            dispatch = _PendingDispatch(
                incident_id=incident_id,
                admission_token=admission.admission_token,
                occurrence_keys=admission.admitted_keys,
                payload=payload_snapshot,
                incident_anchors=deepcopy(incident_anchors),
                handler=handler,
                completion=completion,
            )
            worker = incident_queue.worker
            if worker is not None and (worker.done() or worker.cancelling()):
                incident_queue.deferred.append(dispatch)
                return DispatchSubmission(
                    "queued",
                    incident_id=incident_id,
                    completion=completion,
                )

            incident_queue.pending.append(dispatch)
            if worker is not None:
                return DispatchSubmission(
                    "queued",
                    incident_id=incident_id,
                    completion=completion,
                )

            self._correlator.mark_processing(incident_id, True)
            try:
                self._start_worker(incident_id, incident_queue, loop)
            except _WorkerTaskCreationError as start_error:
                incident_queue.pending.pop()
                self._queues.pop(incident_id, None)
                self._correlator.mark_processing(incident_id, False)
                if not completion.done():
                    completion.cancel()
                try:
                    admission_store.rollback_queued(admission.admission_token)
                except Exception as rollback_error:
                    log.exception(
                        "could not roll back queued incident admission for %s",
                        incident_id,
                    )
                    raise rollback_error from start_error
                raise
            return DispatchSubmission(
                "started",
                incident_id=incident_id,
                completion=completion,
            )

    def _get_admission_store(self) -> IncidentAdmissionStore:
        if self._admission_store is None:
            database_path = self._database_path
            if database_path is None:
                runtime_data_dir = Path(os.getenv("ATLASOPS_RUNTIME_DATA_DIR", "data"))
                database_path = runtime_data_dir / _ADMISSION_DATABASE_NAME
            self._admission_store = IncidentAdmissionStore(database_path)
        return self._admission_store

    @staticmethod
    def _filtered_payload(
        candidate_batch: CorrelatorCandidateBatch,
        admitted_keys: set[str],
    ) -> dict[str, Any]:
        payload = deepcopy(candidate_batch.original_payload)
        alerts = payload.get("alerts")
        if not isinstance(alerts, list):
            return payload

        indices = sorted(
            {
                ref.original_alert_index
                for ref in candidate_batch.occurrence_refs
                if ref.occurrence_key in admitted_keys
                and ref.original_alert_index is not None
            }
        )
        if indices:
            payload["alerts"] = [alerts[index] for index in indices if index < len(alerts)]
        return payload

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
        except Exception as exc:
            worker_coroutine.close()
            raise _WorkerTaskCreationError(str(exc)) from exc
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

                try:
                    self._get_admission_store().mark_attempted(
                        pending.admission_token,
                        pending.occurrence_keys,
                    )
                except Exception as exc:
                    log.exception(
                        "could not mark incident admission attempted for %s; handler was not invoked",
                        incident_id,
                    )
                    if not pending.completion.done():
                        pending.completion.set_exception(exc)
                    pending = None
                    continue

                try:
                    handler_kwargs = {"incident_id": incident_id}
                    if pending.incident_anchors is not None:
                        handler_kwargs["incident_anchors"] = pending.incident_anchors
                    result = await pending.handler(pending.payload, **handler_kwargs)
                except asyncio.CancelledError:
                    if not pending.completion.done():
                        pending.completion.cancel()
                    raise
                except Exception as exc:
                    log.exception(
                        "incident handler failed for %s; attempted occurrences will not be replayed",
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
                except _WorkerTaskCreationError as exc:
                    failed = tuple(incident_queue.pending)
                    incident_queue.pending.clear()
                    self._queues.pop(incident_id, None)
                    self._correlator.mark_processing(incident_id, False)
                    for dispatch in failed:
                        try:
                            self._get_admission_store().rollback_queued(
                                dispatch.admission_token
                            )
                        except Exception:
                            log.exception(
                                "could not roll back queued incident admission for %s",
                                incident_id,
                            )
                        if not dispatch.completion.done():
                            dispatch.completion.set_exception(exc)
                    log.exception(
                        "failed to start deferred incident dispatch for %s",
                        incident_id,
                    )
                except Exception as exc:
                    failed = tuple(incident_queue.pending)
                    incident_queue.pending.clear()
                    self._queues.pop(incident_id, None)
                    self._correlator.mark_processing(incident_id, False)
                    for dispatch in failed:
                        if not dispatch.completion.done():
                            dispatch.completion.set_exception(exc)
                    log.exception(
                        "failed to register deferred incident dispatch for %s",
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
