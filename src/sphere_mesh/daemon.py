"""Persistent connection supervision for the Sphere mesh controller."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Protocol


logger = logging.getLogger("sphere-daemon")


class SupervisedSession(Protocol):
    async def wait_lost(self) -> BaseException: ...

    async def close(self) -> None: ...


class ConnectionSupervisor:
    def __init__(
        self,
        connect: Callable[[], Awaitable[SupervisedSession]],
        *,
        backoff_initial: float = 1.0,
        backoff_max: float = 30.0,
    ) -> None:
        if backoff_initial < 0 or backoff_max < backoff_initial:
            raise ValueError("invalid reconnect backoff")
        self._connect = connect
        self._backoff_initial = backoff_initial
        self._backoff_max = backoff_max
        self._task: asyncio.Task | None = None
        self._changed = asyncio.Condition()
        self._stopping = asyncio.Event()
        self._session: SupervisedSession | None = None
        self._generation = 0
        self.last_failure: BaseException | None = None

    @property
    def session(self) -> SupervisedSession | None:
        return self._session

    @property
    def generation(self) -> int:
        return self._generation

    def start(self) -> None:
        if self._task is not None:
            raise RuntimeError("connection supervisor is already running")
        if self._stopping.is_set():
            raise RuntimeError("connection supervisor has been stopped")
        self._task = asyncio.create_task(self._run())

    async def wait_ready(
        self, *, after_generation: int = 0
    ) -> tuple[int, SupervisedSession]:
        async with self._changed:
            await self._changed.wait_for(
                lambda: (
                    self._session is not None
                    and self._generation > after_generation
                )
                or self._stopping.is_set()
                or (self._task is not None and self._task.done())
            )
            if self._session is None:
                if self._task is None or self._task.cancelled():
                    raise RuntimeError("connection supervisor stopped")
                failure = self._task.exception()
                if failure is not None:
                    raise failure
                raise RuntimeError("connection supervisor stopped")
            return self._generation, self._session

    async def stop(self) -> None:
        self._stopping.set()
        task = self._task
        if task is None:
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self._task = None
        async with self._changed:
            self._changed.notify_all()

    async def _publish(self, session: SupervisedSession | None) -> None:
        async with self._changed:
            self._session = session
            if session is not None:
                self._generation += 1
            self._changed.notify_all()

    async def _pause(self, delay: float) -> None:
        try:
            await asyncio.wait_for(self._stopping.wait(), timeout=delay)
        except TimeoutError:
            pass

    async def _run(self) -> None:
        delay = self._backoff_initial
        try:
            while not self._stopping.is_set():
                try:
                    session = await self._connect()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.last_failure = exc
                    logger.warning("connection attempt failed: %s", exc)
                    await self._pause(delay)
                    delay = min(self._backoff_max, max(delay * 2, 0.001))
                    continue
                delay = self._backoff_initial
                await self._publish(session)
                try:
                    self.last_failure = await session.wait_lost()
                    logger.warning("connection lost: %s", self.last_failure)
                finally:
                    await self._publish(None)
                    await session.close()
                if not self._stopping.is_set():
                    await self._pause(delay)
        finally:
            session = self._session
            await self._publish(None)
            if session is not None:
                await session.close()
