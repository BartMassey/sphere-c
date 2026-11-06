"""Enrollment selection, durable recovery, and batch lifecycle checks."""

import asyncio
from contextlib import asynccontextmanager
import json
from types import SimpleNamespace

import pytest

from sphere_mesh import enrollment as module
from sphere_mesh import provision as provisioning
from sphere_mesh.state import StateStore


def beacon(uuid="dc235212111f50146430647067613068"):
    return SimpleNamespace(uuid=bytes.fromhex(uuid), device=SimpleNamespace(address="DC:23:52:12:11:1F"))


@asynccontextmanager
async def exclusive():
    yield


@pytest.fixture
def manager(tmp_path):
    with StateStore(tmp_path / "state.json") as store:
        state = store.load_or_create()
        yield module.EnrollmentManager(state, store, SimpleNamespace(), exclusive)


@pytest.mark.parametrize("uuid, accepted", [
    ("dc235209a8cf10146430647067613068", True),
    ("dc235212111f50146430647067613068", True),
    ("dc235212111f50140000000000000000", False),
    ("1234", False),
])
def test_product_uuid(uuid, accepted):
    assert module.sphere_uuid(bytes.fromhex(uuid)) is accepted


@pytest.mark.parametrize("raw, provisioning_service, accepted", [
    (module.REVISION_05, True, True),
    (bytes.fromhex("00003032303214100002"), True, False),
    (module.REVISION_05, False, False),
])
async def test_read_only_live_compatibility(raw, provisioning_service, accepted):
    char = SimpleNamespace(uuid=module.REVISION, properties=["read"])
    services = [SimpleNamespace(uuid=module.DEVICE_INFORMATION, characteristics=[char])]
    if provisioning_service:
        services.append(SimpleNamespace(uuid=module.PROVISIONING, characteristics=[]))

    class Client:
        disconnected = False

        async def read_gatt_char(self, characteristic):
            assert characteristic is char
            return raw

        async def disconnect(self):
            self.disconnected = True

    client = Client()
    client.services = services

    async def connect(*args, **kwargs):
        return client

    details = await module.inspect_candidate(SimpleNamespace(connect=connect), beacon())
    assert (details is not None) is accepted
    assert client.disconnected


def test_checkpoint_reserves_full_span_and_retains_keys(manager):
    prov = SimpleNamespace(capabilities=SimpleNamespace(num_elements=6), device_key=b"k" * 16)
    manager._checkpoint(0x100, prov, beacon())
    saved = json.loads(manager.store.path.read_text())
    assert saved["next_unicast"] == 0x106
    assert saved["devices"]["0x0100"]["device_key"] == (b"k" * 16).hex()
    assert saved["devices"]["0x0100"]["enrollment_pending"]
    assert not saved["devices"]["0x0100"]["provisioning_complete"]
    with pytest.raises(ValueError, match="overlaps"):
        manager._checkpoint(0x103, prov, beacon())
    with pytest.raises(ValueError, match="exceeds"):
        manager._checkpoint(0x7fff, prov, beacon())


async def test_stop_finishes_inflight_batch_and_status_has_no_keys(manager, monkeypatch):
    started, finish = asyncio.Event(), asyncio.Event()

    class Scanner:
        async def stop(self):
            pass

    manager.transport.scanner = Scanner

    async def scan(*args, **kwargs):
        return [beacon()]

    async def inspect(*args):
        return {"firmware_version": "0.5"}

    async def commission(*args):
        started.set()
        await finish.wait()
        manager._results.append({"device": "0x0100", "name": "BEN"})

    monkeypatch.setattr(module, "scan_unprovisioned", scan)
    monkeypatch.setattr(module, "inspect_candidate", inspect)
    monkeypatch.setattr(manager, "_commission", commission)
    manager.start(30)
    await started.wait()
    stopping = asyncio.create_task(manager.stop())
    await asyncio.sleep(0)
    assert not stopping.done()
    finish.set()
    result = await stopping
    assert not result["active"]
    assert result["devices"] == [{"device": "0x0100", "name": "BEN"}]
    assert "device_key" not in json.dumps(result)


async def test_configuration_failure_keeps_recovery_record(manager, monkeypatch):
    manager.state["composition"] = {"old": True}
    manager.state["active_device"] = "0x0090"
    manager.state["devices"]["0x0090"] = {"device_key": "ab" * 16, "name": "ARI"}

    async def provision(args, state, path, transport, *, target, checkpoint, activate):
        prov = SimpleNamespace(capabilities=SimpleNamespace(num_elements=6), device_key=b"k" * 16)
        checkpoint(0x100, prov, target)
        state["devices"]["0x0100"]["provisioning_complete"] = True
        return 0x100

    async def configure(address):
        raise TimeoutError("secret exception detail")

    monkeypatch.setattr(module, "provision", provision)
    monkeypatch.setattr(manager, "_configure", configure)
    with pytest.raises(TimeoutError):
        await manager._commission(beacon(), {"firmware_version": "0.5"})
    saved = json.loads(manager.store.path.read_text())
    assert saved["devices"]["0x0100"]["enrollment_pending"]
    assert saved["devices"]["0x0100"]["firmware_version"] == "0.5"
    assert saved["active_device"] == "0x0090"
    assert saved["composition"] == {"old": True}


@pytest.mark.parametrize("complete", [True, False])
async def test_provisioning_checkpoint_precedes_data_transmission(manager, monkeypatch, complete):
    sent = []

    class Pump:
        def __init__(self, *args):
            pass

        def put(self, payload):
            saved = json.loads(manager.store.path.read_text())
            assert saved["devices"]["0x0100"]["device_key"] == (b"k" * 16).hex()
            assert saved["next_unicast"] == 0x106
            sent.append(payload)

        def start(self):
            pass

        async def stop(self):
            pass

    class Provisioner:
        done = complete
        state = SimpleNamespace(name="WAIT_COMPLETE")
        device_key = b"k" * 16
        capabilities = SimpleNamespace(num_elements=6)

        def __init__(self, *args, send, **kwargs):
            self.send = send

        def start(self):
            self.send(b"\x07encrypted")
            if complete:
                self.on_done()

    class Connection:
        async def stop(self):
            pass

        async def disconnect(self):
            pass

    async def open_session(*args, **kwargs):
        return Connection(), Connection()

    monkeypatch.setattr(provisioning, "BearerPump", Pump)
    monkeypatch.setattr(provisioning, "Provisioner", Provisioner)
    monkeypatch.setattr(provisioning, "open_gatt_session", open_session)
    monkeypatch.setattr(provisioning, "PROVISIONING_TIMEOUT_S", 0.001)
    operation = provisioning.provision(
        SimpleNamespace(static_oob=None), manager.state, manager.store.path,
        manager.transport, target=beacon(), checkpoint=manager._checkpoint, activate=False,
    )
    if complete:
        await operation
    else:
        with pytest.raises(provisioning.Phase0Failure):
            await operation
    assert sent == [b"\x07encrypted"]
    saved = json.loads(manager.store.path.read_text())
    assert saved["devices"]["0x0100"]["provisioning_complete"] is complete
    assert saved["devices"]["0x0100"]["device_key"] == (b"k" * 16).hex()
    assert saved["next_unicast"] == 0x106


async def test_configure_does_not_save_new_metadata_as_old_device(manager, monkeypatch):
    manager.state["active_device"] = "0x0090"
    manager.state["devices"]["0x0090"] = {"name": "ARI"}
    manager.state["composition"] = {"old": True}
    manager._checkpoint(0x100, SimpleNamespace(capabilities=SimpleNamespace(num_elements=6),
                                            device_key=b"k" * 16), beacon())

    class Scanner:
        async def stop(self):
            pass

    manager.transport.scanner = Scanner

    async def proxy(*args, **kwargs):
        return SimpleNamespace(device=object()), []

    class Session:
        node, pump = object(), object()

        async def close(self):
            pass

    async def open_session(*args, **kwargs):
        return Session()

    async def configure(node, address, key, working, pump):
        working["composition"] = {"new": True}
        manager.store.advance_sequence(manager.state, 3)
        assert json.loads(manager.store.path.read_text())["composition"] == {"old": True}
        working["official_config_complete"] = True

    monkeypatch.setattr(module, "find_proxy_node", proxy)
    monkeypatch.setattr(module, "open_mesh_proxy_session", open_session)
    monkeypatch.setattr(module, "configure", configure)
    await manager._configure(0x100)
    assert manager.state["composition"] == {"old": True}
    assert manager.state["devices"]["0x0100"]["composition"] == {"new": True}
    assert not manager.state["devices"]["0x0100"]["enrollment_pending"]


@pytest.mark.parametrize("configuration_fails", [False, True])
async def test_configure_closes_radio_when_persistence_fails(manager, monkeypatch, configuration_fails):
    manager._checkpoint(0x100, SimpleNamespace(capabilities=SimpleNamespace(num_elements=6),
                                            device_key=b"k" * 16), beacon())
    cleanup = []

    class Scanner:
        async def stop(self):
            cleanup.append("scanner")

    class Session:
        node, pump = object(), object()

        async def close(self):
            cleanup.append("session")

    manager.transport.scanner = Scanner

    async def proxy(*args, **kwargs):
        return SimpleNamespace(device=object()), []

    async def open_session(*args, **kwargs):
        return Session()

    async def configure(*args):
        if configuration_fails:
            raise TimeoutError("configuration failure")

    def save(*args):
        raise OSError("disk failure")

    monkeypatch.setattr(module, "find_proxy_node", proxy)
    monkeypatch.setattr(module, "open_mesh_proxy_session", open_session)
    monkeypatch.setattr(module, "configure", configure)
    monkeypatch.setattr(manager.store, "save", save)
    expected = TimeoutError if configuration_fails else OSError
    with pytest.raises(expected):
        await manager._configure(0x100)
    assert cleanup == ["session", "scanner"]


async def test_ambiguous_data_transfer_recovers_saved_key_before_new_provision(manager, monkeypatch):
    manager._checkpoint(0x100, SimpleNamespace(capabilities=SimpleNamespace(num_elements=6),
                                            device_key=b"k" * 16), beacon())
    attempted = []

    async def recover(address):
        attempted.append(address)
        entry = manager.state["devices"]["0x0100"]
        assert entry["device_key"] == (b"k" * 16).hex()
        assert not entry["provisioning_complete"]
        entry["provisioning_complete"] = True
        entry["enrollment_pending"] = False
        manager.store.save(manager.state)
        manager._stop.set()

    monkeypatch.setattr(manager, "_configure", recover)
    manager.start(30)
    await manager._task
    assert attempted == [0x100]
    assert manager.state["next_unicast"] == 0x106


async def test_reenrolling_selected_identity_moves_active_after_complete(manager, monkeypatch):
    target = beacon()
    manager.state["active_device"] = "0x0090"
    manager.state["devices"]["0x0090"] = {
        "name": "BEN", "uuid": target.uuid.hex(), "address": target.device.address,
    }
    manager.state["composition"] = {"old": True}

    async def provision(args, state, path, transport, *, target, checkpoint, activate):
        checkpoint(0x100, SimpleNamespace(capabilities=SimpleNamespace(num_elements=6),
                                        device_key=b"k" * 16), target)
        assert state["active_device"] == "0x0090"
        assert "superseded_by" not in state["devices"]["0x0090"]
        state["devices"]["0x0100"]["provisioning_complete"] = True
        return 0x100

    async def configure(address):
        assert manager.state["active_device"] == "0x0100"
        assert "composition" not in manager.state
        assert manager.state["devices"]["0x0090"]["superseded_by"] == "0x0100"
        assert manager.state["devices"]["0x0100"]["name"] == "BEN"

    monkeypatch.setattr(module, "provision", provision)
    monkeypatch.setattr(manager, "_configure", configure)
    await manager._commission(target, {"firmware_version": "0.5"})


def test_checkpoint_rejects_span_crossing_bluez_host_reservation(manager):
    manager.state["bluez_mesh"] = {"unicast": 0x103, "element_count": 1}
    prov = SimpleNamespace(capabilities=SimpleNamespace(num_elements=6), device_key=b"k" * 16)
    with pytest.raises(ValueError, match="BlueZ Mesh host"):
        manager._checkpoint(0x100, prov, beacon())
    assert not manager.state["devices"]
    assert manager.state["next_unicast"] == 0x100


@pytest.mark.parametrize("expire_during", ["inspection", "first_commission"])
async def test_window_expiry_inside_batch_prevents_new_commission(manager, monkeypatch, expire_during):
    now = [0]
    manager._now = lambda: now[0]
    manager._deadline = 10
    commissioned = []

    class Scanner:
        async def stop(self):
            pass

    manager.transport.scanner = Scanner

    async def scan(*args, **kwargs):
        return [beacon(), beacon("dc235209a8cf10146430647067613068")]

    async def inspect(*args):
        if expire_during == "inspection":
            now[0] = 11
        return {"firmware_version": "0.5"}

    async def commission(target, *args):
        commissioned.append(target.uuid)
        now[0] = 11

    monkeypatch.setattr(module, "scan_unprovisioned", scan)
    monkeypatch.setattr(module, "inspect_candidate", inspect)
    monkeypatch.setattr(manager, "_commission", commission)
    await manager._run()
    assert len(commissioned) == (0 if expire_during == "inspection" else 1)


async def test_window_expiry_during_pending_configuration_stops_next_attempt(manager, monkeypatch):
    now = [0]
    manager._now = lambda: now[0]
    manager._deadline = 10
    manager.state["devices"] = {
        "0x0100": {"enrollment_pending": True},
        "0x0106": {"enrollment_pending": True},
    }
    configured = []

    async def configure(address):
        configured.append(address)
        now[0] = 11

    monkeypatch.setattr(manager, "_configure", configure)
    await manager._run()
    assert configured == [0x100]


async def test_pending_uuid_is_not_reprovisioned_and_recovery_backs_off(manager, monkeypatch):
    target = beacon()
    manager._checkpoint(0x100, SimpleNamespace(capabilities=SimpleNamespace(num_elements=6),
                                            device_key=b"k" * 16), target)
    now, scans, configured = [0], [], []
    manager._now = lambda: now[0]
    manager._deadline = 30

    class Scanner:
        async def stop(self):
            pass

    class Stop(asyncio.Event):
        async def wait(self):
            now[0] += 2

    manager._stop = Stop()
    manager.transport.scanner = Scanner

    async def scan(*args, **kwargs):
        scans.append(True)
        now[0] += 1
        if len(scans) == 3:
            manager._stop.set()
        return [target]

    async def configure(address):
        configured.append(address)
        raise TimeoutError()

    async def forbidden(*args, **kwargs):
        pytest.fail("a pending identity must not be inspected or provisioned again")

    monkeypatch.setattr(module, "scan_unprovisioned", scan)
    monkeypatch.setattr(module, "inspect_candidate", forbidden)
    monkeypatch.setattr(manager, "_commission", forbidden)
    monkeypatch.setattr(manager, "_configure", configure)
    await manager._run()
    assert len(scans) == 3
    assert configured == [0x100]
    assert manager.state["next_unicast"] == 0x106
    assert len(manager.state["devices"]) == 1


async def test_whole_window_radio_lease_survives_multiple_scans_and_restores_on_stop(manager, monkeypatch):
    events, scans = [], []

    @asynccontextmanager
    async def radio_lease():
        events.append("paused")
        try:
            yield
        finally:
            events.append("restored")

    class Scanner:
        async def stop(self):
            pass

    class Stop(asyncio.Event):
        async def wait(self):
            pass

    manager = module.EnrollmentManager(manager.state, manager.store,
                                       SimpleNamespace(scanner=Scanner), radio_lease,
                                       hold_radio=True)
    manager._stop = Stop()

    async def scan(*args, **kwargs):
        assert events == ["paused"]
        scans.append(True)
        if len(scans) == 3:
            manager._stop.set()
        return []

    monkeypatch.setattr(module, "scan_unprovisioned", scan)
    manager.start(30)
    await manager._task
    assert len(scans) == 3
    assert events == ["paused", "restored"]
    assert not manager.status()["active"]


@pytest.mark.parametrize("duration", [0, -1, True, float("nan"), float("inf"), 3601, "10"])
async def test_invalid_window(manager, duration):
    with pytest.raises(ValueError):
        manager.start(duration)

@pytest.mark.parametrize("recover_pending", [False, True])
async def test_completed_device_ignores_stale_pairing_beacon(manager, monkeypatch, recover_pending):
    target = beacon()
    now = [0]
    manager._now = lambda: now[0]
    manager._deadline = 20
    if recover_pending:
        manager.state["devices"]["0x0100"] = {
            "uuid": target.uuid.hex(), "enrollment_pending": True,
        }

    class Scanner:
        async def stop(self):
            pass

    class Stop(asyncio.Event):
        async def wait(self):
            now[0] += 2

    manager._stop = Stop()
    manager.transport.scanner = Scanner
    attempts, inspections, scans = [], [], []

    async def scan(*args, **kwargs):
        scans.append(True)
        if len(scans) == 2:
            manager._stop.set()
        return [target]

    async def inspect(*args):
        inspections.append(True)
        return {"firmware_version": "0.5"}

    async def commission(*args):
        attempts.append("commission")

    async def configure(address):
        attempts.append("recovery")
        manager.state["devices"]["0x0100"]["enrollment_pending"] = False

    monkeypatch.setattr(module, "scan_unprovisioned", scan)
    monkeypatch.setattr(module, "inspect_candidate", inspect)
    monkeypatch.setattr(manager, "_commission", commission)
    monkeypatch.setattr(manager, "_configure", configure)
    await manager._run()
    assert attempts == (["recovery"] if recover_pending else ["commission"])
    assert len(inspections) == (0 if recover_pending else 1)
    assert len(scans) == 2


async def test_named_enrollment_requires_explicit_unique_name(manager, monkeypatch):
    found = asyncio.Event()
    target = beacon()
    attempts = []

    class Scanner:
        async def stop(self):
            pass

    async def scan(*args, **kwargs):
        found.set()
        return [target]

    async def inspect(*args):
        return {"firmware_version": "0.5"}

    async def commission(*args):
        attempts.append(manager._candidates[target.uuid.hex()]["name"])
        manager._stop.set()

    manager.transport.scanner = Scanner
    manager.state["devices"]["0x0090"] = {"name": "ARI"}
    monkeypatch.setattr(module, "scan_unprovisioned", scan)
    monkeypatch.setattr(module, "inspect_candidate", inspect)
    monkeypatch.setattr(manager, "_commission", commission)
    manager.start(30, require_names=True)
    await found.wait()
    await asyncio.sleep(0)
    assert attempts == []
    assert manager.status()["candidates"] == [{"candidate": target.uuid.hex(), "name": "", "state": "awaiting_name", "existing_device": None}]
    for invalid in (" ari ", "", "0x1234"):
        with pytest.raises(ValueError):
            manager.name(target.uuid.hex(), invalid)
    assert manager.name(target.uuid.hex(), " BEN ")["candidates"][0]["state"] == "queued"
    with pytest.raises(ValueError):
        manager.name(target.uuid.hex(), "OTHER")
    await asyncio.wait_for(manager._task, 1)
    assert attempts == ["BEN"]
    assert manager.status()["candidates"][0]["state"] == "complete"


async def test_named_candidate_pending_recovery_cannot_be_reprovisioned(manager):
    target = beacon()
    manager.start(30, require_names=True)
    manager._candidates[target.uuid.hex()] = {"beacon": target, "details": {}, "name": "", "state": "failed"}
    manager.state["devices"]["0x0100"] = {"uuid": target.uuid.hex(), "enrollment_pending": True}
    with pytest.raises(ValueError, match="pending recovery"):
        manager.name(target.uuid.hex(), "BEN")
    manager.state["devices"].clear()
    await manager.stop()

async def test_named_checkpoint_persists_reserved_name_before_data(manager):
    target = beacon()
    manager._require_names = True
    manager._candidates[target.uuid.hex()] = {
        "beacon": target, "details": {}, "name": "BEN", "state": "provisioning",
    }
    prov = SimpleNamespace(capabilities=SimpleNamespace(num_elements=1), device_key=b"k" * 16)
    manager._checkpoint(0x100, prov, target)
    saved = json.loads(manager.store.path.read_text())
    assert saved["devices"]["0x0100"]["name"] == "BEN"
    assert saved["devices"]["0x0100"]["enrollment_pending"]


async def test_stop_discards_queued_candidate(manager):
    target = beacon()
    manager._candidates[target.uuid.hex()] = {
        "beacon": target, "details": {}, "name": "BEN", "state": "queued",
    }
    result = await manager.stop()
    assert result["candidates"][0]["state"] == "awaiting_name"


async def test_retry_rediscovers_device_and_keeps_scanner_active_until_gatt_finishes(manager, monkeypatch):
    old, fresh = beacon(), beacon()
    item = {'beacon': old, 'details': {}, 'name': 'Sphere 12111F', 'state': 'retrying'}
    manager._candidates[old.uuid.hex()] = item
    manager._deadline = manager._now() + 30
    running = [False]

    class Scanner:
        async def stop(self):
            running[0] = False

    scanner = Scanner()
    manager.transport.scanner = lambda: scanner

    async def scan(actual, **options):
        assert actual is scanner
        assert options['stop_on'] == old.uuid
        assert options['stop_after'] is False
        running[0] = True
        return [fresh]

    async def commission(target, details):
        assert target is fresh and target is not old
        assert running[0]
        manager._checkpoint(0x100, SimpleNamespace(
            capabilities=SimpleNamespace(num_elements=6), device_key=b'k' * 16,
        ), target)

    monkeypatch.setattr(module, 'scan_unprovisioned', scan)
    monkeypatch.setattr(manager, '_commission', commission)
    assert await manager._commission_with_scan(old.uuid.hex(), item)
    assert not running[0]
    assert item['beacon'] is fresh
    saved = json.loads(manager.store.path.read_text())
    assert saved['devices']['0x0100']['name'] == item['name']
    assert saved['devices']['0x0100']['enrollment_pending']


def test_automatic_connection_retries_are_bounded_and_committed_data_recovers(manager):
    manager._now = lambda: 0
    target = beacon()
    item = {'beacon': target, 'name': 'Sphere 12111F', 'state': 'provisioning'}
    identity = target.uuid.hex()
    for attempt, expected in ((1, 'retrying'), (2, 'retrying'), (3, 'failed')):
        manager._candidate_attempts[identity] = attempt
        manager._candidate_failed(identity, item, module.BearerError('connection lost'))
        assert item['state'] == expected
    manager._checkpoint(0x100, SimpleNamespace(
        capabilities=SimpleNamespace(num_elements=6), device_key=b'k' * 16,
    ), target)
    manager._candidate_failed(identity, item, module.BearerError('completion lost'))
    assert item['state'] == 'recovering'
    assert manager.state['next_unicast'] == 0x106


async def test_automatic_enrollment_uses_active_scan_and_saves_default_name_without_prompt(manager, monkeypatch):
    target = beacon()
    scanning = [False]
    commissioned = []

    class Scanner:
        async def stop(self):
            scanning[0] = False

    manager.transport.scanner = Scanner

    async def scan(*args, **kwargs):
        scanning[0] = True
        return [target]

    async def inspect(*args):
        assert scanning[0]
        return {'firmware_version': '0.5'}

    async def commission(actual, details):
        assert scanning[0]
        assert actual is target
        commissioned.append(actual.uuid)
        manager._checkpoint(0x100, SimpleNamespace(
            capabilities=SimpleNamespace(num_elements=6), device_key=b'k' * 16,
        ), target)
        manager.state['devices']['0x0100']['enrollment_pending'] = False
        manager.state['devices']['0x0100']['official_config_complete'] = True
        manager.store.save(manager.state)
        manager._stop.set()

    monkeypatch.setattr(module, 'scan_unprovisioned', scan)
    monkeypatch.setattr(module, 'inspect_candidate', inspect)
    monkeypatch.setattr(manager, '_commission', commission)
    manager.start(30)
    await manager._task
    assert commissioned == [target.uuid]
    assert not scanning[0]
    saved = json.loads(manager.store.path.read_text())
    name = saved['devices']['0x0100']['name']
    assert name.startswith('Sphere ') and name.strip()
    assert manager.status()['candidates'][0]['name'] == name
    assert manager.status()['candidates'][0]['state'] == 'complete'
    await manager.stop()
    assert '0x0100' in manager.state['devices']

async def test_candidate_visible_during_scan_and_inspection(manager, monkeypatch):
    target = beacon()
    scanning = asyncio.Event()
    finish_scan = asyncio.Event()
    inspecting = asyncio.Event()
    finish_inspection = asyncio.Event()

    class Scanner:
        async def stop(self):
            pass

    async def scan(*args, **kwargs):
        kwargs["on_discovered"](target)
        scanning.set()
        await finish_scan.wait()
        return [target]

    async def inspect(*args):
        inspecting.set()
        await finish_inspection.wait()
        return None

    manager.transport.scanner = Scanner
    monkeypatch.setattr(module, "scan_unprovisioned", scan)
    monkeypatch.setattr(module, "inspect_candidate", inspect)
    manager.start(30)
    await scanning.wait()
    candidate = manager.status()["candidates"][0]
    assert candidate == {"candidate": target.uuid.hex(), "name": manager._name(target),
                         "state": "found", "existing_device": None}
    finish_scan.set()
    await inspecting.wait()
    assert manager.status()["candidates"][0]["state"] == "checking"
    finish_inspection.set()
    await asyncio.sleep(0)
    assert manager.status()["candidates"][0]["state"] == "unsupported"
    await manager.stop()

@pytest.mark.parametrize("blocked_phase", ["inspection", "commission"])
async def test_new_advertisement_visible_during_gatt_work(manager, monkeypatch, blocked_phase):
    first = beacon()
    second = beacon("dc235209a8cf10146430647067613068")
    second.device.address = "DC:23:52:09:A8:CF"
    blocked, release = asyncio.Event(), asyncio.Event()
    inspected, commissioned = [], []

    class Scanner:
        def __init__(self):
            self.callbacks = []

        def register_detection_callback(self, callback):
            self.callbacks.append(callback)
            return lambda: self.callbacks.remove(callback)

        def advertise(self, target):
            advertisement = SimpleNamespace(service_data={module.PROVISIONING: target.uuid + bytes(2)})
            for callback in list(self.callbacks):
                callback(target.device, advertisement)

        async def stop(self):
            pass

    scanner = Scanner()
    manager.transport.scanner = lambda: scanner

    async def scan(*args, **kwargs):
        return [first]

    async def inspect(transport, target):
        inspected.append(target.uuid)
        if blocked_phase == "inspection":
            blocked.set()
            await release.wait()
        return {"firmware_version": "0.5"}

    async def commission(target, details):
        commissioned.append(target.uuid)
        if blocked_phase == "commission":
            blocked.set()
            await release.wait()

    monkeypatch.setattr(module, "scan_unprovisioned", scan)
    monkeypatch.setattr(module, "inspect_candidate", inspect)
    monkeypatch.setattr(manager, "_commission", commission)
    manager.start(30)
    await blocked.wait()
    scanner.advertise(second)
    candidates = {item["candidate"]: item for item in manager.status()["candidates"]}
    assert candidates[second.uuid.hex()]["state"] == "found"
    assert second.uuid not in inspected and second.uuid not in commissioned
    stopping = asyncio.create_task(manager.stop())
    await asyncio.sleep(0)
    release.set()
    await stopping
    assert not scanner.callbacks
    assert second.uuid not in inspected and second.uuid not in commissioned


async def test_immediately_found_candidates_reserve_unique_default_names(manager):
    manager._deadline = manager._now() + 30
    first, second = beacon(), beacon('aa000012111f50146430647067613068')
    second.device.address = 'AA:00:00:12:11:1F'
    manager._discovered(first)
    manager._discovered(second)
    names = [item['name'] for item in manager.status()['candidates']]
    assert len(names) == 2 and len(set(names)) == 2
    assert not manager.state['devices']  # finding alone never provisions
