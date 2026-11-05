import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from sphere_mesh.daemon import ConnectionSupervisor
from sphere_mesh import daemon
from sphere_mesh.state import new_state


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


async def test_proxy_network_registers_current_keys_and_uses_global_sequence(monkeypatch):
    state = new_state()
    state["seq"] = 2048
    state["active_device"] = "0x012a"
    state["devices"] = {
        "0x0100": {"device_key": "00" * 16, "superseded_by": "0x012a"},
        "0x0124": {"device_key": "11" * 16},
        "0x012a": {"device_key": "22" * 16},
        "0x0131": {"device_key": "33" * 16, "enrollment_pending": True},
    }
    scanner = SimpleNamespace(stop=AsyncMock())
    transport = SimpleNamespace(scanner=Mock(return_value=scanner))
    # The proxy need not be the first member or the saved active target.
    proxy = SimpleNamespace(address="another-owned-proxy")
    find = AsyncMock(return_value=(SimpleNamespace(device=proxy), []))
    session = SimpleNamespace(node=SimpleNamespace(add_device=Mock()), close=AsyncMock())
    open_session = AsyncMock(return_value=session)
    monkeypatch.setattr(daemon, "find_proxy_node", find)
    monkeypatch.setattr(daemon, "open_mesh_proxy_session", open_session)
    store = Mock()

    def advance(shared_state, next_sequence):
        assert shared_state is state
        shared_state["seq"] = next_sequence

    store.advance_sequence.side_effect = advance
    result = await daemon.open_proxy_network(transport, state, store)

    assert result is session
    assert find.await_args.args[0] is scanner
    assert find.await_args.kwargs["node_addresses"] == (0x0124, 0x012a)
    assert find.await_args.kwargs["identity_netkey"] == bytes.fromhex(state["netkey"])
    assert find.await_args.kwargs["stop_after"] is False
    assert open_session.await_args.args == (transport, proxy)
    options = open_session.await_args.kwargs
    assert options["unicast"] == 0x0124
    assert options["device_key"] == bytes.fromhex("11" * 16)
    assert options["seq"] == 2048
    assert options["provisioner_addr"] == state["provisioner_addr"]
    session.node.add_device.assert_called_once_with(0x012a, bytes.fromhex("22" * 16))
    options["persist_seq"](2050)
    store.advance_sequence.assert_called_once_with(state, 2050)
    assert state["seq"] == 2050
    scanner.stop.assert_awaited_once()
    session.close.assert_not_awaited()


async def test_proxy_network_closes_session_and_scanner_if_key_registration_fails(monkeypatch):
    state = new_state()
    state["devices"] = {
        "0x0124": {"device_key": "11" * 16},
        "0x012a": {"device_key": "22" * 16},
    }
    scanner = SimpleNamespace(stop=AsyncMock())
    transport = SimpleNamespace(scanner=Mock(return_value=scanner))
    session = SimpleNamespace(
        node=SimpleNamespace(add_device=Mock(side_effect=ValueError("invalid key"))),
        close=AsyncMock(),
    )
    monkeypatch.setattr(daemon, "find_proxy_node", AsyncMock(
        return_value=(SimpleNamespace(device=object()), []),
    ))
    monkeypatch.setattr(daemon, "open_mesh_proxy_session", AsyncMock(return_value=session))
    with pytest.raises(ValueError, match="invalid key"):
        await daemon.open_proxy_network(transport, state, Mock())
    session.close.assert_awaited_once()
    scanner.stop.assert_awaited_once()


@pytest.mark.parametrize("arguments,mode,backend", [
    ([], "proxy", "proxy"),
    (["--mode", "proxy"], "proxy", "proxy"),
    (["--mode", "mesh"], "mesh", "bluez"),
    (["--backend", "proxy"], "proxy", "proxy"),
    (["--backend", "bluez"], "mesh", "bluez"),
])
def test_daemon_mode_flags(arguments, mode, backend):
    args = daemon.parse_args(arguments)
    assert (args.mode, args.backend) == (mode, backend)


@pytest.mark.parametrize("arguments", [
    ["--mode", "mesh", "--backend", "bluez"],
    ["--adapter", "not-an-adapter"],
    ["--mode", "mesh", "--enroll-seconds", "120"],
    ["--enroll-seconds", "3601"],
])
def test_invalid_daemon_modes_fail_before_startup(arguments):
    with pytest.raises(SystemExit) as exc:
        daemon.parse_args(arguments)
    assert exc.value.code == 2


def test_split_mode_preserves_selected_ordinary_adapter():
    args = daemon.parse_args([
        "--mode", "mesh", "--adapter", "aa:bb:cc:dd:ee:ff",
        "--experimental-enrollment", "--enroll-seconds", "120",
    ])
    assert args.adapter == "AA:BB:CC:DD:EE:FF"
    assert args.backend == "bluez"
