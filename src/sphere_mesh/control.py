"""Acknowledged basic control operations for a provisioned Sphere."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from btmesh.access import (
    OP_GENERIC_ONOFF_STATUS,
    encode_opcode,
    generic_onoff_get,
    generic_onoff_set,
    parse_generic_onoff_status,
)
from btmesh.node import MeshNode, ReceivedMessage


class TidAllocator(Protocol):
    def __call__(self) -> int: ...


class ControlUnavailable(RuntimeError):
    """The Sphere did not acknowledge a basic control operation."""


@dataclass(frozen=True)
class OnOffState:
    present: bool
    target: bool | None
    remaining_time: int | None

    @property
    def effective(self) -> bool:
        return self.present if self.target is None else self.target


def _parse_status(message: ReceivedMessage) -> OnOffState:
    payload = encode_opcode(message.opcode) + message.params
    status = parse_generic_onoff_status(payload)
    return OnOffState(
        present=bool(status.present_onoff),
        target=(
            None if status.target_onoff is None else bool(status.target_onoff)
        ),
        remaining_time=status.remaining_time,
    )


class BasicController:
    def __init__(
        self,
        node: MeshNode,
        unicast: int,
        allocate_tid: TidAllocator,
        *,
        timeout: float = 5.0,
    ) -> None:
        self._node = node
        self._unicast = unicast
        self._allocate_tid = allocate_tid
        self._timeout = timeout

    async def read(self) -> OnOffState:
        try:
            response = await self._node.request(
                self._unicast,
                generic_onoff_get(),
                OP_GENERIC_ONOFF_STATUS,
                timeout=self._timeout,
            )
        except TimeoutError as exc:
            raise ControlUnavailable(
                f"Sphere at {self._unicast:#06x} did not answer OnOff Get"
            ) from exc
        return _parse_status(response)

    async def set(self, desired: bool) -> OnOffState:
        tid = self._allocate_tid()
        payload = generic_onoff_set(desired, tid)
        try:
            response = await self._node.request(
                self._unicast,
                payload,
                OP_GENERIC_ONOFF_STATUS,
                timeout=self._timeout,
            )
        except TimeoutError as exc:
            raise ControlUnavailable(
                f"Sphere at {self._unicast:#06x} did not acknowledge "
                f"{'on' if desired else 'off'}"
            ) from exc
        return _parse_status(response)

    async def toggle(self) -> OnOffState:
        current = await self.read()
        return await self.set(not current.effective)

    async def execute(self, command: str) -> OnOffState:
        if command == "read":
            return await self.read()
        if command == "on":
            return await self.set(True)
        if command == "off":
            return await self.set(False)
        if command == "toggle":
            return await self.toggle()
        raise ValueError(f"unknown basic control command: {command}")
