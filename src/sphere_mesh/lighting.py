"""Acknowledged standard lighting controls for a provisioned Sphere."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from btmesh.access import (
    MODEL_LIGHT_LIGHTNESS_SERVER,
    OP_LIGHT_CTL_TEMPERATURE_STATUS,
    OP_LIGHT_LIGHTNESS_STATUS,
    encode_opcode,
    light_ctl_temperature_get,
    light_ctl_temperature_set,
    light_lightness_get,
    light_lightness_set,
    parse_light_ctl_temperature_status,
    parse_light_lightness_status,
)
from btmesh.node import MeshNode, ReceivedMessage

from .access import (
    MODEL_LIGHT_CTL_TEMPERATURE_SERVER,
    MODEL_LIGHT_HSL_SERVER,
    MODEL_LIGHT_XYL_SERVER,
    OP_LIGHT_HSL_STATUS,
    OP_LIGHT_XYL_STATUS,
    light_hsl_get,
    light_hsl_set,
    light_xyl_get,
    light_xyl_set,
    parse_light_hsl_status,
    parse_light_xyl_status,
)
from .control import TidAllocator


class LightingUnsupported(RuntimeError):
    """The node's saved composition lacks the requested server model."""


class LightingUnavailable(RuntimeError):
    """The Sphere did not acknowledge a standard lighting operation."""


@dataclass(frozen=True)
class LightnessState:
    present: int
    target: int | None
    remaining_time: int | None


@dataclass(frozen=True)
class TemperatureState:
    present: int
    delta_uv: int
    target: int | None
    target_delta_uv: int | None
    remaining_time: int | None


@dataclass(frozen=True)
class HslState:
    lightness: int
    hue: int
    saturation: int
    remaining_time: int | None


@dataclass(frozen=True)
class XylState:
    lightness: int
    x: int
    y: int
    remaining_time: int | None


@dataclass(frozen=True)
class LightingCommand:
    model: str
    values: tuple[int, ...] | None

    @property
    def server_model(self) -> int:
        return {
            "lightness": MODEL_LIGHT_LIGHTNESS_SERVER,
            "temperature": MODEL_LIGHT_CTL_TEMPERATURE_SERVER,
            "hsl": MODEL_LIGHT_HSL_SERVER,
            "xyl": MODEL_LIGHT_XYL_SERVER,
        }[self.model]


def parse_lighting_command(value: str) -> LightingCommand:
    """Parse a raw-value lighting CLI command."""
    name, separator, raw_values = value.partition("=")
    arity = {"lightness": 1, "temperature": 1, "hsl": 3, "xyl": 3}
    if name not in arity:
        raise ValueError(f"unknown lighting model: {name}")
    if not separator:
        return LightingCommand(name, None)
    try:
        values = tuple(int(item, 0) for item in raw_values.split(","))
    except ValueError as exc:
        raise ValueError(f"{name} values must be integers") from exc
    if len(values) != arity[name]:
        raise ValueError(f"{name} Set expects {arity[name]} value(s)")
    if name == "temperature":
        if not 800 <= values[0] <= 20000:
            raise ValueError("temperature must be 800..20000 kelvin")
    elif any(not 0 <= item <= 0xFFFF for item in values):
        raise ValueError(f"{name} values must be 0..65535")
    return LightingCommand(name, values)


def model_element(composition: dict, model_id: int) -> int:
    """Return the address of the element hosting a SIG server model."""
    for element in composition.get("elements", []):
        if model_id in element.get("sig_models", []):
            return element["address"]
    raise LightingUnsupported(
        f"saved composition has no SIG server model 0x{model_id:04x}"
    )


def _payload(message: ReceivedMessage) -> bytes:
    return encode_opcode(message.opcode) + message.params


class LightingController:
    def __init__(
        self,
        node: MeshNode,
        composition: dict,
        allocate_tid: TidAllocator,
        *,
        timeout: float = 5.0,
    ) -> None:
        self._node = node
        self._composition = composition
        self._allocate_tid = allocate_tid
        self._timeout = timeout

    async def _request(
        self,
        model_id: int,
        payload: bytes,
        response_opcode: int,
        operation: str,
    ) -> ReceivedMessage:
        address = model_element(self._composition, model_id)
        try:
            return await self._node.request(
                address,
                payload,
                response_opcode,
                timeout=self._timeout,
            )
        except TimeoutError as exc:
            raise LightingUnavailable(
                f"Sphere element {address:#06x} did not answer {operation}"
            ) from exc

    async def read_lightness(self) -> LightnessState:
        response = await self._request(
            MODEL_LIGHT_LIGHTNESS_SERVER,
            light_lightness_get(),
            OP_LIGHT_LIGHTNESS_STATUS,
            "Lightness Get",
        )
        status = parse_light_lightness_status(_payload(response))
        return LightnessState(
            status.present_lightness,
            status.target_lightness,
            status.remaining_time,
        )

    async def set_lightness(self, lightness: int) -> LightnessState:
        address = model_element(
            self._composition, MODEL_LIGHT_LIGHTNESS_SERVER
        )
        tid = self._allocate_tid()
        try:
            response = await self._node.request(
                address,
                light_lightness_set(lightness, tid),
                OP_LIGHT_LIGHTNESS_STATUS,
                timeout=self._timeout,
            )
        except TimeoutError as exc:
            raise LightingUnavailable(
                f"Sphere element {address:#06x} did not acknowledge "
                "Lightness Set"
            ) from exc
        status = parse_light_lightness_status(_payload(response))
        return LightnessState(
            status.present_lightness,
            status.target_lightness,
            status.remaining_time,
        )

    async def read_temperature(self) -> TemperatureState:
        response = await self._request(
            MODEL_LIGHT_CTL_TEMPERATURE_SERVER,
            light_ctl_temperature_get(),
            OP_LIGHT_CTL_TEMPERATURE_STATUS,
            "CTL Temperature Get",
        )
        return self._temperature_state(response)

    async def set_temperature(
        self, temperature: int, delta_uv: int = 0
    ) -> TemperatureState:
        address = model_element(
            self._composition, MODEL_LIGHT_CTL_TEMPERATURE_SERVER
        )
        tid = self._allocate_tid()
        try:
            response = await self._node.request(
                address,
                light_ctl_temperature_set(temperature, delta_uv, tid),
                OP_LIGHT_CTL_TEMPERATURE_STATUS,
                timeout=self._timeout,
            )
        except TimeoutError as exc:
            raise LightingUnavailable(
                f"Sphere element {address:#06x} did not acknowledge "
                "CTL Temperature Set"
            ) from exc
        return self._temperature_state(response)

    @staticmethod
    def _temperature_state(response: ReceivedMessage) -> TemperatureState:
        status = parse_light_ctl_temperature_status(_payload(response))
        return TemperatureState(
            status.present_temperature,
            status.present_delta_uv,
            status.target_temperature,
            status.target_delta_uv,
            status.remaining_time,
        )

    async def read_hsl(self) -> HslState:
        response = await self._request(
            MODEL_LIGHT_HSL_SERVER,
            light_hsl_get(),
            OP_LIGHT_HSL_STATUS,
            "HSL Get",
        )
        return HslState(*parse_light_hsl_status(_payload(response)))

    async def set_hsl(
        self, lightness: int, hue: int, saturation: int
    ) -> HslState:
        address = model_element(self._composition, MODEL_LIGHT_HSL_SERVER)
        tid = self._allocate_tid()
        try:
            response = await self._node.request(
                address,
                light_hsl_set(lightness, hue, saturation, tid),
                OP_LIGHT_HSL_STATUS,
                timeout=self._timeout,
            )
        except TimeoutError as exc:
            raise LightingUnavailable(
                f"Sphere element {address:#06x} did not acknowledge HSL Set"
            ) from exc
        return HslState(*parse_light_hsl_status(_payload(response)))

    async def read_xyl(self) -> XylState:
        response = await self._request(
            MODEL_LIGHT_XYL_SERVER,
            light_xyl_get(),
            OP_LIGHT_XYL_STATUS,
            "xyL Get",
        )
        return XylState(*parse_light_xyl_status(_payload(response)))

    async def set_xyl(self, lightness: int, x: int, y: int) -> XylState:
        address = model_element(self._composition, MODEL_LIGHT_XYL_SERVER)
        tid = self._allocate_tid()
        try:
            response = await self._node.request(
                address,
                light_xyl_set(lightness, x, y, tid),
                OP_LIGHT_XYL_STATUS,
                timeout=self._timeout,
            )
        except TimeoutError as exc:
            raise LightingUnavailable(
                f"Sphere element {address:#06x} did not acknowledge xyL Set"
            ) from exc
        return XylState(*parse_light_xyl_status(_payload(response)))

    async def execute(
        self, command: LightingCommand
    ) -> LightnessState | TemperatureState | HslState | XylState:
        if command.model == "lightness":
            if command.values is None:
                return await self.read_lightness()
            return await self.set_lightness(command.values[0])
        if command.model == "temperature":
            if command.values is None:
                return await self.read_temperature()
            return await self.set_temperature(command.values[0])
        if command.model == "hsl":
            if command.values is None:
                return await self.read_hsl()
            return await self.set_hsl(*command.values)
        if command.model == "xyl":
            if command.values is None:
                return await self.read_xyl()
            return await self.set_xyl(*command.values)
        raise ValueError(f"unknown lighting model: {command.model}")
