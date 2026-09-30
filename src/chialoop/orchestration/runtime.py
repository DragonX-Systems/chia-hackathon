"""Asynchronous local and CHIA runtimes with a common completion interface."""

from __future__ import annotations

import os
import signal
import time
import traceback
from dataclasses import dataclass, field
from multiprocessing import get_all_start_methods, get_context
from multiprocessing.connection import wait as wait_for_connections
from typing import Any, Protocol, Sequence

from ..core.types import JobResult, JobSpec
from .nodes import ENGINE_NODES, JobExecutor


@dataclass(frozen=True)
class TaskHandle:
    job: JobSpec
    opaque: Any
    submitted_monotonic: float
    submitted_epoch_seconds: float


@dataclass(frozen=True)
class CompletedTask:
    handle: TaskHandle
    result: JobResult


class AsyncRuntime(Protocol):
    def submit(self, job: JobSpec, executor: JobExecutor) -> TaskHandle: ...

    def wait_first(
        self,
        handles: Sequence[TaskHandle],
        *,
        timeout_seconds: float | None = None,
    ) -> list[CompletedTask]: ...

    def consumed_compute_hours(
        self, handle: TaskHandle, *, now: float | None = None
    ) -> float: ...

    def started_epoch_seconds(self, handle: TaskHandle) -> float | None: ...

    def cancel(self, handle: TaskHandle) -> None: ...

    def close(self) -> None: ...


@dataclass
class _LocalProcessTask:
    process: Any
    receiver: Any
    started_monotonic: Any
    started_epoch_seconds: Any
    result: JobResult | None = field(default=None)


def _run_local_process(
    job: JobSpec,
    executor: JobExecutor,
    sender: Any,
    started_monotonic: Any,
    started_epoch_seconds: Any,
) -> None:
    """Execute one local job behind a process boundary so it can be killed."""

    # Place the worker and any simulator it launches in their own process
    # group. Cancelling only the Python worker can otherwise orphan a live
    # simulator, which consumes a slot after ChiaLoop records a timeout.
    if hasattr(os, "setsid"):
        os.setsid()
    started_monotonic.value = time.monotonic()
    started_epoch_seconds.value = time.time()
    try:
        node = ENGINE_NODES[job.engine]
        local_function = getattr(node, "_chia_original", node)
        sender.send(("result", local_function(job, executor)))
    except BaseException as exc:  # pragma: no cover - defensive process boundary
        sender.send(
            (
                "error",
                f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}",
            )
        )
    finally:
        sender.close()


class LocalAsyncRuntime:
    """Process-isolated fallback with hard cancellation and async completion."""

    def __init__(self, max_workers: int) -> None:
        if max_workers <= 0:
            raise ValueError("max_workers must be positive")
        method = "fork" if "fork" in get_all_start_methods() else "spawn"
        self._context = get_context(method)
        self._max_workers = max_workers
        self._tasks: dict[int, _LocalProcessTask] = {}

    def submit(self, job: JobSpec, executor: JobExecutor) -> TaskHandle:
        active = sum(task.process.is_alive() for task in self._tasks.values())
        if active >= self._max_workers:
            raise RuntimeError("local runtime has no free worker process")
        receiver, sender = self._context.Pipe(duplex=False)
        started_monotonic = self._context.Value("d", 0.0)
        started_epoch_seconds = self._context.Value("d", 0.0)
        process = self._context.Process(
            target=_run_local_process,
            args=(
                job,
                executor,
                sender,
                started_monotonic,
                started_epoch_seconds,
            ),
            name=f"chialoop-{job.job_id}",
        )
        submitted_monotonic = time.monotonic()
        submitted_epoch_seconds = time.time()
        process.start()
        sender.close()
        task = _LocalProcessTask(
            process=process,
            receiver=receiver,
            started_monotonic=started_monotonic,
            started_epoch_seconds=started_epoch_seconds,
        )
        self._tasks[process.pid] = task
        return TaskHandle(
            job,
            task,
            submitted_monotonic,
            submitted_epoch_seconds,
        )

    def wait_first(
        self,
        handles: Sequence[TaskHandle],
        *,
        timeout_seconds: float | None = None,
    ) -> list[CompletedTask]:
        if not handles:
            return []
        receiver_to_handle = {
            handle.opaque.receiver: handle for handle in handles
        }
        ready = wait_for_connections(
            list(receiver_to_handle),
            timeout=timeout_seconds,
        )
        completed: list[CompletedTask] = []
        for receiver in ready:
            handle = receiver_to_handle[receiver]
            task = handle.opaque
            try:
                kind, payload = receiver.recv()
            except EOFError as exc:
                raise RuntimeError(
                    f"local worker for {handle.job.job_id} exited without a result"
                ) from exc
            finally:
                receiver.close()
                task.process.join()
                self._tasks.pop(task.process.pid, None)
            if kind == "error":
                raise RuntimeError(
                    f"local worker for {handle.job.job_id} failed: {payload}"
                )
            task.result = payload
            completed.append(CompletedTask(handle, payload))
        return sorted(completed, key=lambda item: item.result.end_time)

    def consumed_compute_hours(
        self, handle: TaskHandle, *, now: float | None = None
    ) -> float:
        task = handle.opaque
        if task.result is not None:
            return max(0.0, float(task.result.compute_hours))
        started = float(task.started_monotonic.value)
        if started <= 0:
            return 0.0
        now = time.monotonic() if now is None else now
        return max(0.0, now - started) / 3600.0

    def started_epoch_seconds(self, handle: TaskHandle) -> float | None:
        started = float(handle.opaque.started_epoch_seconds.value)
        return started if started > 0 else None

    def cancel(self, handle: TaskHandle) -> None:
        task = handle.opaque
        self._stop_task(task)

    def close(self) -> None:
        for task in list(self._tasks.values()):
            self._stop_task(task)
        self._tasks.clear()

    def _stop_task(self, task: _LocalProcessTask) -> None:
        process = task.process
        if process.is_alive():
            isolated_group = False
            if hasattr(os, "getpgid") and hasattr(os, "killpg"):
                try:
                    isolated_group = os.getpgid(process.pid) == process.pid
                except ProcessLookupError:
                    pass
            if isolated_group:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            else:
                process.terminate()
            process.join(timeout=2.0)
            if process.is_alive():
                if isolated_group:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    process.kill()
                process.join()
        task.receiver.close()
        self._tasks.pop(process.pid, None)


class ChiaAsyncRuntime:
    """Real non-blocking ``chia_remote`` runtime.

    This path intentionally disables stuck-task retries and other appendix
    behavior. It waits for the first completion, then drains other results
    already ready before the controller makes another scheduling decision.
    """

    def __init__(self) -> None:
        try:
            import ray  # type: ignore
            from chia.base.ChiaFunction import (  # type: ignore
                TrackedRef,
                chia_cancel,
                chia_wait,
                get,
            )
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise RuntimeError("CHIA is not installed; use LocalAsyncRuntime") from exc
        self._TrackedRef = TrackedRef
        self._chia_cancel = chia_cancel
        self._chia_wait = chia_wait
        self._get = get
        self._ray = ray

        @ray.remote(num_cpus=0)
        class _ExecutionStartRegistry:
            def __init__(self) -> None:
                self._starts: dict[str, float] = {}

            def mark(self, job_id: str, epoch_seconds: float) -> None:
                self._starts[job_id] = epoch_seconds

            def started(self, job_id: str) -> float | None:
                return self._starts.get(job_id)

            def clear(self, job_id: str) -> None:
                self._starts.pop(job_id, None)

        self._start_registry = _ExecutionStartRegistry.remote()

    def submit(self, job: JobSpec, executor: JobExecutor) -> TaskHandle:
        node = ENGINE_NODES[job.engine]
        ref = node.chia_remote(job, executor, self._start_registry)
        tracked = self._TrackedRef(ref=ref, label=job.job_id)
        return TaskHandle(job, tracked, time.monotonic(), time.time())

    def wait_first(
        self,
        handles: Sequence[TaskHandle],
        *,
        timeout_seconds: float | None = None,
    ) -> list[CompletedTask]:
        if not handles:
            return []
        ready, pending = self._chia_wait(
            [handle.opaque for handle in handles],
            num_returns=1,
            timeout=timeout_seconds,
            retry=False,
        )
        by_identity = {id(handle.opaque): handle for handle in handles}
        completed: list[CompletedTask] = []
        while ready:
            for tracked in ready:
                handle = by_identity[id(tracked)]
                completed.append(CompletedTask(handle, self._get(tracked.ref)))
                self._start_registry.clear.remote(handle.job.job_id)
            if not pending:
                break
            ready, pending = self._chia_wait(
                pending,
                num_returns=len(pending),
                timeout=0,
                retry=False,
            )
        return completed

    def consumed_compute_hours(
        self, handle: TaskHandle, *, now: float | None = None
    ) -> float:
        del now  # The worker registry reports epoch timestamps.
        started = self.started_epoch_seconds(handle)
        if started is None:
            return 0.0
        return max(0.0, time.time() - started) / 3600.0

    def started_epoch_seconds(self, handle: TaskHandle) -> float | None:
        started = self._get(
            self._start_registry.started.remote(handle.job.job_id)
        )
        return float(started) if started is not None else None

    def cancel(self, handle: TaskHandle) -> None:
        self._chia_cancel(handle.opaque.ref, force=True)
        self._start_registry.clear.remote(handle.job.job_id)

    def close(self) -> None:
        try:
            self._ray.kill(self._start_registry, no_restart=True)
        except Exception:  # pragma: no cover - cluster may already be down
            pass
