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
import re
import sys
from types import SimpleNamespace

from btmesh.crypto import k3
from btmesh.bearer import BearerError

from .provision import Phase0Failure, configure, provision
from .transport import (
    bluetooth_error_summary, disconnect_quietly, find_proxy_node, open_mesh_proxy_session,
    scan_unprovisioned,
    watch_unprovisioned,
)


logger = logging.getLogger("spherosphere-enrollment")
PROVISIONING = "00001827-0000-1000-8000-00805f9b34fb"
DEVICE_INFORMATION = "0000180a-0000-1000-8000-00805f9b34fb"
REVISION = "00002a26-0000-1000-8000-00805f9b34fb"
MANUFACTURER = "00002a29-0000-1000-8000-00805f9b34fb"
MODEL = "00002a24-0000-1000-8000-00805f9b34fb"
PRODUCT_ID = bytes.fromhex("6430647067613068")
REVISION_05 = bytes.fromhex("00003035303514100002")
ROOT_METADATA = ("composition", "control_model", "official_config_complete")
PROXY_IDENTITY_PREFERENCE_SECONDS = 2.0


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


def validate_name(name, devices, *, uuid=None, address=None, reserved=()):
    """Normalize a unique, unambiguous public device name."""
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 64:
        raise ValueError("name must contain 1..64 characters")
    name = name.strip()
    if re.fullmatch(r"0[xX][0-9a-fA-F]+", name):
        raise ValueError("name must not be an address")
    folded = name.casefold()
    for entry in devices.values():
        if entry.get("superseded_by") or entry.get("forgotten"):
            continue
        if ((uuid and entry.get("uuid") == uuid) or
                (address and entry.get("address", "").lower() == address.lower())):
            continue
        if str(entry.get("name", "")).strip().casefold() == folded:
            raise ValueError("Device name already exists")
    if any(other.strip().casefold() == folded for other in reserved):
        raise ValueError("Device name already exists")
    return name


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
        self._completed: set[str] = set()
        self._require_names = False
        self._candidates: dict[str, dict] = {}
        self._wake = asyncio.Event()
        self._candidate_attempts: dict[str, int] = {}
        self._candidate_retry_at: dict[str, float] = {}
        self._retry_at: dict[int, float] = {}
        self._retry_count: dict[int, int] = {}
        self._failed_proxy_addresses: dict[int, str] = {}

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
                "devices": deepcopy(self._results), "errors": deepcopy(self._errors),
                "require_names": self._require_names,
                "candidates": [{"candidate": key, "name": item["name"],
                                "state": item["state"], "existing_device": item.get("existing_device")}
                               for key, item in self._candidates.items()]}

    def start(self, duration: float = 120, require_names: bool = False) -> dict:
        """Enable an enrollment window, without asking per-device questions."""
        if not isinstance(require_names, bool):
            raise ValueError("require_names must be a boolean")
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
        self._completed = set()
        self._require_names = require_names
        self._candidates = {}
        self._wake.clear()
        self._candidate_attempts = {}
        self._candidate_retry_at = {}
        self._phase = "scanning"
        logger.info("enrollment window started for %.1fs", duration)
        self._task = asyncio.create_task(self._run())
        return self.status()

    def name(self, candidate: str, name: str) -> dict:
        """Reserve a name and queue exactly one inspected candidate."""
        if not self._window_open() or not self._require_names or self._task is None or self._task.done():
            raise ValueError("named enrollment is not active")
        if not isinstance(candidate, str) or candidate not in self._candidates:
            raise ValueError("unknown candidate")
        item = self._candidates[candidate]
        if any(entry.get("enrollment_pending") and not entry.get("superseded_by")
               and not entry.get("forgotten") and
               (entry.get("uuid") == candidate or entry.get("address", "").lower() ==
                item["beacon"].device.address.lower()) for entry in self.state["devices"].values()):
            raise ValueError("candidate has pending recovery")
        if item["state"] not in ("awaiting_name", "failed"):
            raise ValueError("candidate is already queued or provisioned")
        name = validate_name(name, self.state["devices"], uuid=candidate,
                             address=item["beacon"].device.address,
                             reserved=[other["name"] for key, other in self._candidates.items()
                                       if key != candidate and other["state"] in ("queued", "provisioning")])
        item.update(name=name, state="queued")
        self._wake.set()
        return self.status()

    async def stop(self) -> dict:
        """Stop discovery and await any already-started commissioning."""
        self._stop.set()
        self._wake.set()
        if self._task is not None:
            await asyncio.shield(self._task)
        for item in self._candidates.values():
            if item["state"] == "queued":
                item["state"] = "awaiting_name"
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
            "name": (self._candidates[beacon.uuid.hex()]["name"]
                     if beacon.uuid.hex() in self._candidates
                     else self._name(beacon)),
        }
        self.state["next_unicast"] = address + count
        self.store.save(self.state)

    def _name(self, beacon) -> str:
        previous = [entry for entry in self.state["devices"].values()
                    if entry.get("address", "").lower() == beacon.device.address.lower()
                    and entry.get("name") and not entry.get("superseded_by")
                    and not entry.get("forgotten")]
        if previous:
            return previous[-1]["name"]
        base = "Sphere " + beacon.device.address.replace(":", "")[-6:].upper()
        names = {str(entry.get("name", "")).casefold() for entry in self.state["devices"].values()}
        names.update(str(item.get("name", "")).casefold()
                     for identity, item in self._candidates.items() if identity != beacon.uuid.hex())
        name, suffix = base, 2
        while name.casefold() in names:
            name = f"{base} {suffix}"
            suffix += 1
        return name

    async def _configure(self, address: int) -> None:
        key = f"0x{address:04x}"
        entry = self.state["devices"][key]
        started = self._now()
        stage = "Mesh Proxy scan"
        failed_proxy = self._failed_proxy_addresses.get(address)
        logger.info("looking for Mesh Proxy for %s at %s (provisioning_complete=%s)",
                    entry.get("address"), key, bool(entry.get("provisioning_complete")))
        if failed_proxy is not None:
            logger.info("deprioritizing Mesh Proxy %s for %s after prior failure", failed_proxy, key)
        scanner = self.transport.scanner()
        session = None
        config_state = dict(self.state)
        for field in ROOT_METADATA:
            config_state.pop(field, None)
        try:
            netkey = bytes.fromhex(self.state["netkey"])
            proxy, candidates = await find_proxy_node(
                scanner, k3(netkey), timeout=10, stop_after=False,
                identity_netkey=netkey, node_addresses=(address,),
                prefer_node_identity_for=PROXY_IDENTITY_PREFERENCE_SECONDS,
                last_failed_address=failed_proxy,
            )
            if proxy is None:
                logger.info("no matching Mesh Proxy for %s among %d advertising devices",
                            key, len(candidates))
                raise RuntimeError("new node proxy not found")
            logger.info("matched Mesh Proxy for %s after %.2fs", key, self._now() - started)
            try:
                stage = "Mesh Proxy connection"
                session = await open_mesh_proxy_session(
                    self.transport, proxy.device, netkey=netkey,
                    appkey=bytes.fromhex(self.state["appkey"]),
                    device_key=bytes.fromhex(entry["device_key"]),
                    iv_index=self.state["iv_index"],
                    provisioner_addr=self.state["provisioner_addr"],
                    unicast=address, seq=self.state["seq"],
                    persist_seq=lambda value: self.store.advance_sequence(self.state, value),
                )
                logger.info("Mesh Proxy connected for %s after %.2fs", key, self._now() - started)
                stage = "Mesh configuration"
                await configure(session.node, address, bytes.fromhex(self.state["appkey"]),
                                config_state, session.pump)
            except Exception:
                self._failed_proxy_addresses[address] = proxy.device.address
                raise
            self._failed_proxy_addresses.pop(address, None)
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
        except Exception as exc:
            operation = exc.phase if isinstance(exc, Phase0Failure) else stage
            logger.warning("%s failed for %s after %.2fs (%s)",
                           operation, key, self._now() - started, bluetooth_error_summary(exc))
            raise
        finally:
            try:
                original_error = sys.exception()
                try:
                    self.store.save(self.state)
                except Exception as exc:
                    if original_error is None:
                        raise
                    logger.warning("enrollment persistence failed for %s (%s)", key, type(exc).__name__)
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
                            logger.warning("enrollment cleanup failed for %s (%s)",
                                           key, bluetooth_error_summary(exc))
                if cleanup_error is not None:
                    raise cleanup_error
        logger.info("finished Mesh configuration for %s after %.2fs", key, self._now() - started)

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
            for item in self._candidates.values():
                if item["state"] == "queued":
                    item["state"] = "awaiting_name"
            self._phase = "idle"
            logger.info("enrollment window ended (discovered=%d, enrolled=%d, errors=%d)",
                        len(self._candidates), len(self._results), len(self._errors))

    async def _commission_with_scan(self, identity: str, item: dict) -> bool:
        """Refresh the BLE object and keep discovery alive through GATT handoff.

        A retry (or a legacy naming pause) can outlive the original scan.
        BlueZ may remove its device object when discovery stops; reuse of
        that BLEDevice then fails before provisioning begins.
        """
        scanner = self.transport.scanner()
        completed = False
        unsubscribe = watch_unprovisioned(scanner, self._discovered)
        try:
            beacons = await scan_unprovisioned(
                scanner, timeout=min(10, max(0, self._deadline - self._now())),
                stop_on=bytes.fromhex(identity), stop_after=False,
                on_discovered=self._discovered if unsubscribe is None else None,
            )
            if not self._window_open():
                item["state"] = "awaiting_name"
                return False
            previous_address = item["beacon"].device.address.lower()
            fresh = next((beacon for beacon in beacons
                          if beacon.uuid.hex() == identity
                          and beacon.device.address.lower() == previous_address), None)
            if fresh is None:
                raise BearerError("named Sphere is no longer advertising for provisioning")
            item["beacon"] = fresh
            await self._commission(fresh, item["details"])
            completed = True
            return True
        finally:
            original_error = sys.exception()
            if unsubscribe is not None:
                unsubscribe()
            try:
                await scanner.stop()
            except Exception as exc:
                if not completed and original_error is None:
                    raise
                logger.warning("enrollment scanner cleanup failed (%s)", type(exc).__name__)

    def _candidate_failed(self, identity: str, item: dict, exc: Exception) -> None:
        """Retry bounded pre-Data failures; preserve committed recovery instead."""
        pending = any(
            entry.get("enrollment_pending") and not entry.get("superseded_by")
            and not entry.get("forgotten")
            and (entry.get("uuid") == identity or
                 entry.get("address", "").lower() == item["beacon"].device.address.lower())
            for entry in self.state["devices"].values()
        )
        if pending:
            item["state"] = "recovering"
        elif not self._require_names and self._candidate_attempts.get(identity, 0) < 3:
            item["state"] = "retrying"
            self._candidate_retry_at[identity] = self._now() + 3
        else:
            item["state"] = "failed"
        self._error(item["beacon"].device.address, exc)

    def _candidate_complete(self, identity: str, item: dict) -> None:
        self._completed.add(identity)
        item["state"] = "complete"
        self._errors = [error for error in self._errors
                        if error["address"] != item["beacon"].device.address]

    def _discovered(self, beacon) -> None:
        """Publish a likely Sphere as soon as its advertisement arrives."""
        identity = beacon.uuid.hex()
        if (not self._window_open() or not sphere_uuid(beacon.uuid)
                or identity in self._rejected or identity in self._completed
                or identity in self._candidates):
            return
        if any(entry.get("enrollment_pending") and not entry.get("superseded_by")
               and not entry.get("forgotten")
               and (entry.get("uuid") == identity or
                    entry.get("address", "").lower() == beacon.device.address.lower())
               for entry in self.state["devices"].values()):
            return
        existing = next(((key, entry) for key, entry in self.state["devices"].items()
                         if not entry.get("superseded_by") and not entry.get("forgotten")
                         and (entry.get("uuid") == identity or
                              entry.get("address", "").lower() == beacon.device.address.lower())), None)
        self._candidates[identity] = {
            "beacon": beacon, "details": None,
            "name": existing[1].get("name", "") if existing else self._name(beacon),
            "existing_device": existing[0] if existing else None, "state": "found",
        }
        logger.info("found Sphere advertising for provisioning at %s", beacon.device.address)

    async def _run_passes(self) -> None:
        try:
            while self._window_open():
                async with nullcontext() if self._hold_radio else self.exclusive():
                    # Retry a previously interrupted configuration before
                    # allocating a new address. Saved DeviceKeys are retained.
                    pending = [(int(key, 16), entry) for key, entry in self.state["devices"].items()
                               if entry.get("enrollment_pending") and not entry.get("superseded_by")
                               and not entry.get("forgotten")]
                    for address, entry in pending:
                        if not self._window_open():
                            break
                        if self._retry_at.get(address, 0) > self._now():
                            continue
                        self._phase = "configuring"
                        try:
                            await self._configure(address)
                            self._errors = [error for error in self._errors
                                            if error["address"] != entry.get("address")]
                            if entry.get("uuid"):
                                self._completed.add(entry["uuid"])
                                if entry["uuid"] in self._candidates:
                                    self._candidates[entry["uuid"]]["state"] = "complete"
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
                    for identity, item in list(self._candidates.items()):
                        if not self._window_open():
                            break
                        if item["state"] not in ("queued", "retrying"):
                            continue
                        if self._candidate_retry_at.get(identity, 0) > self._now():
                            continue
                        item["state"] = "provisioning"
                        self._phase = "provisioning"
                        self._candidate_attempts[identity] = self._candidate_attempts.get(identity, 0) + 1
                        try:
                            if not await self._commission_with_scan(identity, item):
                                continue
                            self._candidate_complete(identity, item)
                        except Exception as exc:
                            self._candidate_failed(identity, item, exc)
                    if not self._window_open():
                        break
                    self._phase = "scanning"
                    scanner = self.transport.scanner()
                    unsubscribe = watch_unprovisioned(scanner, self._discovered)
                    try:
                        beacons = await scan_unprovisioned(
                            scanner, timeout=min(3, self._deadline - self._now()),
                            stop_after=False,
                            on_discovered=self._discovered if unsubscribe is None else None,
                        )
                        for beacon in beacons:
                            if not self._window_open():
                                break
                            identity = beacon.uuid.hex()
                            self._discovered(beacon)
                            item = self._candidates.get(identity)
                            if item is None or item["state"] != "found":
                                continue
                            item["beacon"] = beacon
                            item["state"] = "checking"
                            self._phase = "checking"
                            try:
                                details = await inspect_candidate(self.transport, beacon)
                                if details is None:
                                    self._rejected.add(identity)
                                    item["state"] = "unsupported"
                                    continue
                                item["details"] = details
                                if not self._window_open():
                                    item["state"] = "found"
                                    break
                                if self._require_names:
                                    if item["existing_device"] is None:
                                        item["name"] = ""
                                    item["state"] = "awaiting_name"
                                    continue
                                self._phase = "provisioning"
                                item["state"] = "provisioning"
                                self._candidate_attempts[identity] = 1
                                try:
                                    # The discovery scanner is still active:
                                    # no human naming pause interrupts GATT.
                                    await self._commission(beacon, details)
                                    self._candidate_complete(identity, item)
                                except Exception as exc:
                                    self._candidate_failed(identity, item, exc)
                            except Exception as exc:
                                item["state"] = "found"
                                self._error(beacon.device.address, exc)
                    finally:
                        if unsubscribe is not None:
                            unsubscribe()
                        await scanner.stop()
                # Yield between scans. With a per-pass lease, the supervisor
                # and ordinary commands can run; a whole-window lease keeps
                # them paused until enrollment ends.
                if not self._window_open():
                    break
                waiters = [asyncio.create_task(event.wait()) for event in (self._stop, self._wake)]
                try:
                    await asyncio.wait(waiters, timeout=min(2, self._deadline - self._now()),
                                       return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for waiter in waiters:
                        waiter.cancel()
                    await asyncio.gather(*waiters, return_exceptions=True)
                    self._wake.clear()
        except Exception as exc:
            self._error(None, exc)
        finally:
            self._phase = "idle"

    def _error(self, address, exc) -> None:
        # Some provisioning exceptions include cryptographic values. Public
        # progress contains only type and device address, never exception text.
        logger.warning("enrollment %s failed for %s (%s)",
                       self._phase, address, bluetooth_error_summary(exc))
        self._errors.append({"address": address, "error": type(exc).__name__})
        self._errors = self._errors[-20:]
