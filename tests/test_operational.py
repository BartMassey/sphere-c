"""Isolated service checks that never open a Bluetooth adapter."""

import asyncio
import signal
import stat
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from btmesh.access import OP_GENERIC_ONOFF_STATUS
from btmesh.node import ReceivedMessage
from sphere_mesh import daemon
from sphere_mesh.api import request
from sphere_mesh.state import StateLockedError, StateStore, new_state


async def test_service_owns_state_reconnects_and_serializes_requests(
    tmp_path, monkeypatch
):
    state_path = tmp_path / "state" / "state.json"
    socket_path = tmp_path / "run" / "control.sock"
    state = new_state()
    state["active_device"] = "0x0118"
    state["devices"]["0x0118"] = {"device_key": "00" * 16}
    with StateStore(state_path) as store:
        store.save(state)

    entered = asyncio.Event()
    release = asyncio.Event()
    active_requests = 0
    max_active_requests = 0

    async def node_request(*_args, **_kwargs):
        nonlocal active_requests, max_active_requests
        active_requests += 1
        max_active_requests = max(max_active_requests, active_requests)
        entered.set()
        await release.wait()
        active_requests -= 1
        return ReceivedMessage(
            src=0x0118, opcode=OP_GENERIC_ONOFF_STATUS, params=b"\x01"
        )

    class Session:
        def __init__(self):
            self.node = SimpleNamespace(request=node_request)
            self.lost = asyncio.Event()
            self.close = AsyncMock()

        async def wait_lost(self):
            await self.lost.wait()
            return OSError("simulated link loss")

    sessions = [Session(), Session()]
    connections = iter(sessions)

    class Transport:
        async def start(self):
            pass

        async def stop(self):
            pass

        def scanner(self):
            return SimpleNamespace(stop=AsyncMock())

    async def find_proxy(*_args, **_kwargs):
        return SimpleNamespace(device=object()), None

    async def open_proxy(*_args, **_kwargs):
        return next(connections)

    original_supervisor = daemon.ConnectionSupervisor
    monkeypatch.setattr(daemon, "LocalTransport", Transport)
    monkeypatch.setattr(daemon, "find_proxy_node", find_proxy)
    monkeypatch.setattr(daemon, "open_mesh_proxy_session", open_proxy)
    monkeypatch.setattr(
        daemon,
        "ConnectionSupervisor",
        lambda connect: original_supervisor(
            connect, backoff_initial=0, backoff_max=0
        ),
    )
    callbacks = {}
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(
        loop,
        "add_signal_handler",
        lambda signum, callback: callbacks.setdefault(signum, callback),
    )

    service = asyncio.create_task(
        daemon.run_service(
            SimpleNamespace(state=state_path, socket=socket_path)
        )
    )
    try:
        async def connected_after(generation):
            async def ready():
                while True:
                    if service.done():
                        raise AssertionError(f"service exited: {service.result()}")
                    if socket_path.exists():
                        health = await request(socket_path, "health")
                        result = health["result"]
                        if result["connected"] and result["generation"] > generation:
                            return result
                    await asyncio.sleep(0.01)

            return await asyncio.wait_for(ready(), timeout=2)

        first_health = await connected_after(0)
        assert first_health["generation"] == 1
        assert first_health["device"] == "0x0118"
        assert stat.S_IMODE(socket_path.stat().st_mode) == 0o600
        with pytest.raises(StateLockedError):
            StateStore(state_path).acquire()

        first = asyncio.create_task(
            request(socket_path, "control", {"command": "read"}, request_id=1)
        )
        await asyncio.wait_for(entered.wait(), timeout=1)
        second = asyncio.create_task(
            request(socket_path, "control", {"command": "read"}, request_id=2)
        )
        await asyncio.sleep(0.05)
        assert active_requests == 1
        release.set()
        responses = await asyncio.wait_for(
            asyncio.gather(first, second), timeout=1
        )
        assert [response["ok"] for response in responses] == [True, True]
        assert max_active_requests == 1

        sessions[0].lost.set()
        second_health = await connected_after(1)
        assert second_health["generation"] == 2
        assert second_health["last_failure"] == "simulated link loss"
        sessions[0].close.assert_awaited_once()
    finally:
        if signal.SIGTERM in callbacks:
            callbacks[signal.SIGTERM]()
        else:
            service.cancel()
        await asyncio.wait_for(service, timeout=2)

    sessions[1].close.assert_awaited_once()
    assert not socket_path.exists()
    with StateStore(state_path) as store:
        assert store.load_or_create()["seq"] == 0
