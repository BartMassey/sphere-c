"""Daemon-owned, opt-in commissioning of nearby unprovisioned Spheres.

Discovery and version reads are selection heuristics, not proof of physical
ownership. No reset command or lighting Set is part of enrollment.
"""

from __future__ import annotations

import asyncio
from contextlib import nullcontext
from copy import deepcopy
import logging
import math
import sys
from types import SimpleNamespace

from btmesh.crypto import k3

from .provision import configure, provision
from .transport import (
    disconnect_quietly, find_proxy_node, open_mesh_proxy_session,
    scan_unprovisioned,
)


logger = logging.getLogger("sphere-enrollment")
PROVISIONING = "00001827-0000-1000-8000-00805f9b34fb"
DEVICE_INFORMATION = "0000180a-0000-1000-8000-00805f9b34fb"
REVISION = "00002a26-0000-1000-8000-00805f9b34fb"
MANUFACTURER = "00002a29-0000-1000-8000-00805f9b34fb"
MODEL = "00002a24-0000-1000-8000-00805f9b34fb"
PRODUCT_ID = bytes.fromhex("6430647067613068")
REVISION_05 = bytes.fromhex("00003035303514100002")
ROOT_METADATA = ("composition", "control_model", "official_config_complete")


def sphere_uuid(uuid: bytes) -> bool:
    """Match the observed Sphere C product identifier, not a BLE name."""
    return len(uuid) == 16 and uuid[8:] == PRODUCT_ID


async def inspect_candidate(transport, beacon) -> dict | None:
    """Read live GATT services and revision before any provisioning write."""
    if not sphere_uuid(beacon.uuid):
        return None
    client = await transport.connect(beacon.device, timeout=15)
    try:
        services = list(client.services)
        if not any(service.uuid.lower() == PROVISIONING for service in services):
            return None
        chars = {
            char.uuid.lower(): char
            for service in services
            if service.uuid.lower() == DEVICE_INFORMATION
            for char in service.characteristics
        }
        revision = chars.get(REVISION)
        if revision is None or "read" not in revision.properties:
            return None
        raw = bytes(await client.read_gatt_char(revision))
        if raw != REVISION_05:
            return None
        result = {"firmware_version": "0.5", "firmware_revision_raw": raw.hex()}
        for uuid, field in ((MANUFACTURER, "manufacturer"), (MODEL, "model")):
            char = chars.get(uuid)
            if char is not None and "read" in char.properties:
                result[field] = bytes(await client.read_gatt_char(char)).rstrip(b"\0").decode(
                    "utf-8", errors="replace"
                )
        # Optional strings can vary; the product UUID and exact firmware
        # record are the compatibility criteria established on ARI and BEN.
        return result
    finally:
        await disconnect_quietly(client)


class EnrollmentManager:
    """Commission batches using the daemon's existing locked state store.

    ``exclusive`` is an async context manager factory supplied by the daemon.
    It pauses the proxy supervisor and serializes radio/state work against
    ordinary commands. Stopping finishes an in-flight commissioning attempt
    so the device cannot lose its newly derived recovery key.
    ``hold_radio=True`` keeps that lease until the window closes, avoiding
    repeated advertising/relay restarts between short discovery scans.
    """

    def __init__(self, state: dict, store, transport, exclusive, *, hold_radio=False) -> None:
        self.state = state
        self.store = store
        self.transport = transport
        self.exclusive = exclusive
        self._hold_radio = hold_radio
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._deadline = 0.0
        self._phase = "idle"
        self._results: list[dict] = []
        self._errors: list[dict] = []
        self._rejected: set[str] = set()
        self._retry_at: dict[int, float] = {}
        self._retry_count: dict[int, int] = {}

    def _now(self) -> float:
        return asyncio.get_running_loop().time()

    def _window_open(self) -> bool:
        """Authorize a new operation, separately from finishing one in flight."""
        return not self._stop.is_set() and self._now() < self._deadline

    def status(self) -> dict:
        """Return public progress; never expose keys or arbitrary exceptions."""
        active = self._task is not None and not self._task.done()
        remaining = max(0, self._deadline - self._now()) if active else 0
        return {"active": active, "phase": self._phase,
                "remaining_seconds": round(remaining, 1),
                "devices": deepcopy(self._results), "errors": deepcopy(self._errors)}

    def start(self, duration: float = 120) -> dict:
        """Enable an enrollment window, without asking per-device questions."""
        if isinstance(duration, bool) or not isinstance(duration, (int, float)):
            raise ValueError("duration must be a number of seconds")
        if not math.isfinite(duration) or not 1 <= duration <= 3600:
            raise ValueError("duration must be between 1 and 3600 seconds")
        if self._task is not None and not self._task.done():
            raise ValueError("enrollment is already active")
        self._stop.clear()
        self._deadline = self._now() + duration
        self._results = []
        self._errors = []
        self._rejected = set()
        self._phase = "scanning"
        self._task = asyncio.create_task(self._run())
        return self.status()

    async def stop(self) -> dict:
        """Stop discovery and await any already-started commissioning."""
        self._stop.set()
        if self._task is not None:
            await asyncio.shield(self._task)
        return self.status()

    async def close(self) -> None:
        """Release enrollment work before the daemon releases its state lock."""
        await self.stop()

    def _checkpoint(self, address, prov, beacon) -> None:
        count = prov.capabilities.num_elements
        if not 1 <= count <= 255 or address + count > 0x8000:
            raise ValueError("node element span exceeds available unicast addresses")
        key = f"0x{address:04x}"
        for primary, entry in self.state["devices"].items():
            start = int(primary, 16)
            span = entry.get("element_count")
            if span is None:
                span = len(entry.get("composition", {}).get("elements", [])) or 1
            end = start + span
            if max(start, address) < min(end, address + count):
                raise ValueError("node element span overlaps a saved allocation")
        host = self.state.get("bluez_mesh")
        if host is not None:
            start = host["unicast"]
            end = start + host.get("element_count", 1)
            if max(start, address) < min(end, address + count):
                raise ValueError("node element span overlaps the BlueZ Mesh host")
        if address <= self.state["provisioner_addr"] < address + count:
            raise ValueError("node element span overlaps the GATT controller")
        self.state["devices"][key] = {
            "device_key": prov.device_key.hex(), "uuid": beacon.uuid.hex(),
            "address": beacon.device.address, "element_count": count,
            "enrollment_pending": True, "provisioning_complete": False,
            "official_config_complete": False,
            "name": self._name(beacon),
        }
        self.state["next_unicast"] = address + count
        self.store.save(self.state)

    def _name(self, beacon) -> str:
        previous = [entry for entry in self.state["devices"].values()
                    if entry.get("address", "").lower() == beacon.device.address.lower()
                    and entry.get("name") and not entry.get("superseded_by")]
        if previous:
            return previous[-1]["name"]
        base = "Sphere " + beacon.device.address.replace(":", "")[-6:].upper()
        names = {entry.get("name") for entry in self.state["devices"].values()}
        name, suffix = base, 2
        while name in names:
            name = f"{base} {suffix}"
            suffix += 1
        return name

    async def _configure(self, address: int) -> None:
        key = f"0x{address:04x}"
        entry = self.state["devices"][key]
        scanner = self.transport.scanner()
        session = None
        config_state = dict(self.state)
        for field in ROOT_METADATA:
            config_state.pop(field, None)
        try:
            netkey = bytes.fromhex(self.state["netkey"])
            proxy, _ = await find_proxy_node(
                scanner, k3(netkey), timeout=10, stop_after=False,
                identity_netkey=netkey, node_addresses=(address,),
            )
            if proxy is None:
                raise RuntimeError("new node proxy not found")
            session = await open_mesh_proxy_session(
                self.transport, proxy.device, netkey=netkey,
                appkey=bytes.fromhex(self.state["appkey"]),
                device_key=bytes.fromhex(entry["device_key"]),
                iv_index=self.state["iv_index"],
                provisioner_addr=self.state["provisioner_addr"],
                unicast=address, seq=self.state["seq"],
                persist_seq=lambda value: self.store.advance_sequence(self.state, value),
            )
            await configure(session.node, address, bytes.fromhex(self.state["appkey"]),
                            config_state, session.pump)
            self._supersede(key)
            if self.state.get("active_device") is None:
                self.state["active_device"] = key
            for field in ROOT_METADATA:
                if field in config_state:
                    entry[field] = deepcopy(config_state[field])
                    if self.state.get("active_device") == key:
                        self.state[field] = deepcopy(config_state[field])
            entry["enrollment_pending"] = False
            entry["provisioning_complete"] = True
            self._results.append({"device": key, "name": entry["name"],
                                  "firmware_version": entry.get("firmware_version")})
        finally:
            try:
                original_error = sys.exception()
                try:
                    self.store.save(self.state)
                except Exception as exc:
                    if original_error is None:
                        raise
                    logger.warning("enrollment persistence failed (%s)", type(exc).__name__)
            finally:
                # Persistence failure must not strand a GATT connection or
                # scanner. Preserve the operational/persistence exception if
                # cleanup also fails, while attempting both cleanup actions.
                pending_error = sys.exception()
                cleanup_error = None
                actions = ([session.close] if session is not None else []) + [scanner.stop]
                for cleanup in actions:
                    try:
                        await cleanup()
                    except BaseException as exc:
                        if pending_error is None and cleanup_error is None:
                            cleanup_error = exc
                        else:
                            logger.warning("enrollment cleanup failed (%s)", type(exc).__name__)
                if cleanup_error is not None:
                    raise cleanup_error

    def _supersede(self, key: str) -> None:
        entry = self.state["devices"][key]
        for old_key, old in self.state["devices"].items():
            if old_key != key and (old.get("uuid") == entry["uuid"] or
                    old.get("address", "").lower() == entry["address"].lower()):
                old["superseded_by"] = key
                if self.state.get("active_device") == old_key:
                    self.state["active_device"] = key
                    for field in ROOT_METADATA:
                        self.state.pop(field, None)

    async def _commission(self, beacon, details) -> None:
        args = SimpleNamespace(device_uuid=beacon.uuid.hex(), static_oob=None)
        def checkpoint(address, prov, target):
            self._checkpoint(address, prov, target)
            self.state["devices"][f"0x{address:04x}"].update(details)
            self.store.save(self.state)

        address = await provision(args, self.state, self.store.path, self.transport,
                                  target=beacon, checkpoint=checkpoint, activate=False)
        entry = self.state["devices"][f"0x{address:04x}"]
        entry.update(details)
        entry["enrollment_pending"] = True
        self._supersede(f"0x{address:04x}")
        self.store.save(self.state)
        await self._configure(address)

    async def _run(self) -> None:
        """Optionally retain the radio lease for the entire enrollment window."""
        try:
            async with self.exclusive() if self._hold_radio else nullcontext():
                await self._run_passes()
        except Exception as exc:
            self._error(None, exc)
        finally:
            self._phase = "idle"

    async def _run_passes(self) -> None:
        try:
            while self._window_open():
                async with nullcontext() if self._hold_radio else self.exclusive():
                    # Retry a previously interrupted configuration before
                    # allocating a new address. Saved DeviceKeys are retained.
                    pending = [(int(key, 16), entry) for key, entry in self.state["devices"].items()
                               if entry.get("enrollment_pending") and not entry.get("superseded_by")]
                    for address, entry in pending:
                        if not self._window_open():
                            break
                        if self._retry_at.get(address, 0) > self._now():
                            continue
                        self._phase = "configuring"
                        try:
                            await self._configure(address)
                            self._retry_at.pop(address, None)
                            self._retry_count.pop(address, None)
                        except Exception as exc:
                            self._error(entry.get("address"), exc)
                            attempts = self._retry_count.get(address, 0) + 1
                            self._retry_count[address] = attempts
                            delay = min(120, 10 * 2 ** min(attempts - 1, 4))
                            self._retry_at[address] = self._now() + delay
                        # An unavailable node must not occupy an entire batch
                        # with repeated configuration timeouts. Give discovery
                        # and ordinary daemon commands a turn after one retry.
                        break
                    if not self._window_open():
                        break
                    self._phase = "scanning"
                    scanner = self.transport.scanner()
                    try:
                        beacons = await scan_unprovisioned(
                            scanner, timeout=min(3, self._deadline - self._now()),
                            stop_after=False,
                        )
                        for beacon in beacons:
                            if not self._window_open():
                                break
                            identity = beacon.uuid.hex()
                            if identity in self._rejected or not sphere_uuid(beacon.uuid):
                                continue
                            # A Data PDU may have reached the lamp even when
                            # Complete was lost. Preserve its reserved address
                            # and key, and attempt configuration recovery only.
                            # A stale beacon must not allocate another identity.
                            if any(entry.get("enrollment_pending")
                                   and not entry.get("superseded_by")
                                   and (entry.get("uuid") == identity or
                                        entry.get("address", "").lower() == beacon.device.address.lower())
                                   for entry in self.state["devices"].values()):
                                continue
                            self._phase = "checking"
                            try:
                                details = await inspect_candidate(self.transport, beacon)
                                if details is None:
                                    self._rejected.add(identity)
                                    continue
                                if not self._window_open():
                                    break
                                self._phase = "provisioning"
                                await self._commission(beacon, details)
                            except Exception as exc:
                                self._error(beacon.device.address, exc)
                    finally:
                        await scanner.stop()
                # Yield between scans. With a per-pass lease, the supervisor
                # and ordinary commands can run; a whole-window lease keeps
                # them paused until enrollment ends.
                if not self._window_open():
                    break
                try:
                    await asyncio.wait_for(
                        self._stop.wait(), timeout=min(2, self._deadline - self._now())
                    )
                except TimeoutError:
                    pass
        except Exception as exc:
            self._error(None, exc)
        finally:
            self._phase = "idle"

    def _error(self, address, exc) -> None:
        # Some provisioning exceptions include cryptographic values. Public
        # progress contains only type and device address, never exception text.
        logger.warning("enrollment attempt failed for %s (%s)", address, type(exc).__name__)
        self._errors.append({"address": address, "error": type(exc).__name__})
        self._errors = self._errors[-20:]
