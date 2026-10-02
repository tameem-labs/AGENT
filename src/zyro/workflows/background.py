"""Background worker for scheduled and durable workflow execution."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime

from zyro.workflows.scheduler import LocalWorkflowScheduler

logger = logging.getLogger(__name__)


class BackgroundSchedulerWorker:
    """Async background worker running scheduled workflows and missed execution recovery."""

    def __init__(
        self,
        scheduler: LocalWorkflowScheduler,
        *,
        interval_seconds: float = 5.0,
    ) -> None:
        self._scheduler = scheduler
        self._interval = max(0.5, interval_seconds)
        self._running = False
        self._task: asyncio.Task[None] | None = None

    @property
    def is_running(self) -> bool:
        return self._running and self._task is not None and not self._task.done()

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        # Immediate missed execution recovery check on startup
        try:
            executed = self._scheduler.run_due(datetime.now(UTC))
            if executed:
                count = len(executed)
                logger.info("Scheduler startup recovered %d missed workflows: %s", count, executed)
        except Exception:
            logger.exception("Scheduler startup recovery check encountered an error")

        self._task = asyncio.create_task(self._run_loop())

    async def _run_loop(self) -> None:
        while self._running:
            try:
                self._scheduler.run_due(datetime.now(UTC))
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error during scheduled workflow evaluation")

            try:
                await asyncio.sleep(self._interval)
            except asyncio.CancelledError:
                break

    def stop_sync(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None


__all__ = ["BackgroundSchedulerWorker"]

