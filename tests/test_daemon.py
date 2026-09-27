import asyncio
from unittest.mock import AsyncMock

import pytest

from sphere_mesh.daemon import ConnectionSupervisor


class FakeSession:
    def __init__(self):
        self.lost = asyncio.Future()
        self.close = AsyncMock()

    async def wait_lost(self):
        return await self.lost


async def test_supervisor_reconnects_after_session_loss():
    first = FakeSession()
    second = FakeSession()
    outcomes = iter([OSError("radio unavailable"), first, second])

    async def connect():
        outcome = next(outcomes)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    supervisor = ConnectionSupervisor(
        connect, backoff_initial=0, backoff_max=0
    )
    supervisor.start()
    generation, session = await asyncio.wait_for(
        supervisor.wait_ready(), timeout=1
    )
    assert generation == 1
    assert session is first

    first.lost.set_result(OSError("link dropped"))
    generation, session = await asyncio.wait_for(
        supervisor.wait_ready(after_generation=generation), timeout=1
    )

    assert generation == 2
    assert session is second
    first.close.assert_awaited_once()
    await supervisor.stop()
    second.close.assert_awaited_once()


async def test_supervisor_never_publishes_overlapping_sessions():
    first = FakeSession()
    second = FakeSession()
    outcomes = iter([first, second])

    async def connect():
        return next(outcomes)

    supervisor = ConnectionSupervisor(
        connect, backoff_initial=0, backoff_max=0
    )
    supervisor.start()
    first_generation, _ = await supervisor.wait_ready()
    first.lost.set_result(OSError("gone"))
    second_generation, current = await asyncio.wait_for(
        supervisor.wait_ready(after_generation=first_generation), timeout=1
    )

    assert second_generation == 2
    assert current is second
    first.close.assert_awaited_once()
    await supervisor.stop()


def test_supervisor_rejects_invalid_backoff():
    async def connect():
        raise AssertionError

    with pytest.raises(ValueError, match="invalid reconnect backoff"):
        ConnectionSupervisor(connect, backoff_initial=2, backoff_max=1)


async def test_wait_ready_fails_after_supervisor_stops():
    session = FakeSession()

    async def connect():
        return session

    supervisor = ConnectionSupervisor(connect)
    supervisor.start()
    await supervisor.wait_ready()
    await supervisor.stop()

    with pytest.raises(RuntimeError, match="stopped"):
        await supervisor.wait_ready(after_generation=1)
    with pytest.raises(RuntimeError, match="has been stopped"):
        supervisor.start()
