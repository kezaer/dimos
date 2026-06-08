# Copyright 2025-2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Reusable hardware preflight policies."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Protocol

from dimos.core.coordination.preflight import PreflightResult
from dimos.core.global_config import GlobalConfig


@dataclass(frozen=True)
class HardwareCandidate:
    """A hardware endpoint discovered before a blueprint starts."""

    address: str
    identity: str | None = None
    interface: str | None = None
    transport: str | None = None
    metadata: Mapping[str, str | None] = field(default_factory=lambda: MappingProxyType({}))


class DiscoverHardware(Protocol):
    def __call__(self, timeout: float) -> Sequence[HardwareCandidate]: ...


class ProbeHardware(Protocol):
    def __call__(self, address: str, timeout: float) -> bool: ...


@dataclass(frozen=True)
class SingleHardwareTargetPreflight:
    """Resolve one configured hardware address from discovery and reachability evidence."""

    name: str
    config_key: str
    discover: DiscoverHardware
    probe: ProbeHardware
    virtual_values: frozenset[str]
    is_virtual_mode: Callable[[GlobalConfig], bool]
    discovery_timeout: float = 1.5
    connect_timeout: float = 0.8
    allow_discovered_but_probe_failed: bool = False
    allow_configured_replacement: bool = False
    missing_guidance: str = "Pass the hardware address explicitly or connect to the robot LAN."
    diagnostic_guidance: str | None = None

    def __call__(self, config: GlobalConfig) -> PreflightResult:
        configured_address = _configured_address(config, self.config_key)
        if self.is_virtual_mode(config) or _is_virtual_value(
            configured_address, self.virtual_values
        ):
            return PreflightResult.ok()

        if configured_address is not None and self._probe_reachable(configured_address):
            return PreflightResult.ok()

        try:
            candidates = self._discover_candidates()
        except Exception as exc:
            return self._fail(f"discovery failed: {exc}. {self._diagnostic_guidance()}")
        if configured_address is None:
            return self._resolve_missing_address(candidates)
        return self._repair_configured_address(configured_address, candidates)

    def _discover_candidates(self) -> tuple[HardwareCandidate, ...]:
        return tuple(self.discover(self.discovery_timeout))

    def _resolve_missing_address(
        self,
        candidates: tuple[HardwareCandidate, ...],
    ) -> PreflightResult:
        if len(candidates) == 1:
            candidate = candidates[0]
            return self._candidate_update(
                candidate,
                f"selected the only discovered {self.name}",
            )
        if not candidates:
            return self._fail(
                f"no configured {self.config_key} and discovery found no {self.name}. "
                f"{self.missing_guidance}"
            )
        return self._fail(
            f"multiple {self.name} devices discovered: {_candidate_list(candidates)}. "
            f"Set {self.config_key} explicitly."
        )

    def _repair_configured_address(
        self,
        configured_address: str,
        candidates: tuple[HardwareCandidate, ...],
    ) -> PreflightResult:
        matching = [
            candidate for candidate in candidates if candidate.address == configured_address
        ]
        if matching:
            return self._probe_failed_result(
                f"discovery found configured {self.name} at {configured_address}, "
                "but the reachability probe did not respond.",
                address=configured_address,
            )

        if len(candidates) == 1:
            if not self.allow_configured_replacement:
                return self._fail(
                    f"{configured_address} did not respond; discovered {self.name} "
                    f"{_candidate_list(candidates)}. Set {self.config_key} explicitly "
                    "to use a different device."
                )
            candidate = candidates[0]
            return self._candidate_update(
                candidate,
                f"replaced unreachable configured {self.config_key} {configured_address} "
                f"with discovered {self.name} {candidate.address}",
            )

        if not candidates:
            return self._fail(
                f"{configured_address} did not respond and discovery found no {self.name}. "
                f"{self._diagnostic_guidance()}"
            )
        return self._fail(
            f"{configured_address} did not respond; discovered {self.name} devices: "
            f"{_candidate_list(candidates)}. Set {self.config_key} explicitly."
        )

    def _candidate_update(self, candidate: HardwareCandidate, reason: str) -> PreflightResult:
        if self._probe_reachable(candidate.address):
            return PreflightResult.ok(
                config_updates={self.config_key: candidate.address},
                notes=(f"{reason}; using {self.config_key}={candidate.address}",),
            )
        return self._probe_failed_result(
            f"{reason}, but the reachability probe for {candidate.address} did not respond.",
            config_updates={self.config_key: candidate.address},
        )

    def _probe_reachable(self, address: str) -> bool:
        try:
            return self.probe(address, self.connect_timeout)
        except Exception:
            return False

    def _probe_failed_result(
        self,
        warning: str,
        *,
        config_updates: Mapping[str, str] | None = None,
        address: str | None = None,
    ) -> PreflightResult:
        if self.allow_discovered_but_probe_failed:
            diagnostic_address = address
            if diagnostic_address is None and config_updates is not None:
                diagnostic_address = config_updates.get(self.config_key)
            warning = (
                f"{warning} Continuing on discovery evidence only. "
                f"{self._diagnostic_guidance(diagnostic_address)}"
            )
            return PreflightResult.ok(
                config_updates=config_updates,
                warnings=(warning,),
            )
        diagnostic_address = address
        if diagnostic_address is None and config_updates is not None:
            diagnostic_address = config_updates.get(self.config_key)
        return self._fail(f"{warning} {self._diagnostic_guidance(diagnostic_address)}")

    def _fail(self, message: str) -> PreflightResult:
        return PreflightResult.fail(f"{self.name} preflight could not resolve hardware. {message}")

    def _diagnostic_guidance(self, address: str | None = None) -> str:
        guidance = self.diagnostic_guidance or self.missing_guidance
        if address is None:
            return guidance
        return guidance.replace("<ip>", address).replace("{address}", address)


@dataclass(frozen=True)
class MultiHardwareTargetPreflight:
    """Resolve a comma-separated list of hardware addresses from discovery evidence."""

    name: str
    config_key: str
    discover: DiscoverHardware
    probe: ProbeHardware
    virtual_values: frozenset[str]
    is_virtual_mode: Callable[[GlobalConfig], bool]
    discovery_timeout: float = 1.5
    connect_timeout: float = 0.8
    min_targets: int = 1
    allow_discovered_but_probe_failed: bool = False
    allow_discovered_selection: bool = False
    missing_guidance: str = "Pass hardware addresses explicitly or connect to the robot LAN."
    diagnostic_guidance: str | None = None

    def __call__(self, config: GlobalConfig) -> PreflightResult:
        configured_addresses = _configured_addresses(config, self.config_key)
        if self.is_virtual_mode(config) or _all_virtual_values(
            configured_addresses, self.virtual_values
        ):
            return PreflightResult.ok()
        if _has_mixed_virtual_values(configured_addresses, self.virtual_values):
            return self._fail(
                f"{self.config_key} mixes virtual and real addresses: "
                f"{', '.join(configured_addresses)}."
            )
        duplicates = _duplicates(configured_addresses)
        if duplicates:
            return self._fail(
                f"{self.config_key} contains duplicate addresses: {', '.join(duplicates)}."
            )

        if configured_addresses and self._all_reachable(configured_addresses):
            return PreflightResult.ok()

        try:
            candidates = self._discover_candidates()
        except Exception as exc:
            return self._fail(f"discovery failed: {exc}. {self._diagnostic_guidance()}")

        if not configured_addresses:
            return self._resolve_missing_addresses(candidates)
        return self._repair_configured_addresses(configured_addresses, candidates)

    def _discover_candidates(self) -> tuple[HardwareCandidate, ...]:
        return tuple(self.discover(self.discovery_timeout))

    def _resolve_missing_addresses(
        self,
        candidates: tuple[HardwareCandidate, ...],
    ) -> PreflightResult:
        if not candidates:
            return self._fail(
                f"no configured {self.config_key} and discovery found no {self.name}. "
                f"{self.missing_guidance}"
            )
        if not self.allow_discovered_selection:
            return self._fail(
                f"no configured {self.config_key}; discovered {self.name} devices: "
                f"{_candidate_list(candidates)}. Set {self.config_key} explicitly "
                "to the intended fleet."
            )
        return self._candidate_update(
            candidates,
            f"selected {len(candidates)} discovered {self.name} device(s)",
        )

    def _repair_configured_addresses(
        self,
        configured_addresses: tuple[str, ...],
        candidates: tuple[HardwareCandidate, ...],
    ) -> PreflightResult:
        if not candidates:
            return self._fail(
                f"{', '.join(configured_addresses)} did not all respond and discovery "
                f"found no {self.name}. {self._diagnostic_guidance(configured_addresses)}"
            )

        candidate_addresses = tuple(candidate.address for candidate in candidates)
        if set(candidate_addresses) == set(configured_addresses):
            if self._all_reachable(candidate_addresses):
                return PreflightResult.ok(
                    notes=(
                        f"verified configured {self.config_key} after discovery retry: "
                        f"{', '.join(configured_addresses)}",
                    )
                )
            unreachable = tuple(
                address for address in candidate_addresses if not self._probe_reachable(address)
            )
            return self._probe_failed_result(
                f"discovery found configured {self.name} devices, but reachability "
                f"probes failed for {', '.join(unreachable)}.",
                addresses=unreachable,
            )

        if len(candidate_addresses) == len(configured_addresses):
            if not self.allow_discovered_selection:
                return self._fail(
                    f"{', '.join(configured_addresses)} did not all respond; discovered "
                    f"{self.name} devices: {_candidate_list(candidates)}. Set {self.config_key} "
                    "explicitly to the intended fleet."
                )
            return self._candidate_update(
                candidates,
                f"replaced unreachable configured {self.config_key} "
                f"{','.join(configured_addresses)} with discovered {self.name} devices",
            )

        return self._fail(
            f"{', '.join(configured_addresses)} did not all respond; discovered "
            f"{self.name} devices: {_candidate_list(candidates)}. Set {self.config_key} "
            "explicitly to the intended fleet."
        )

    def _candidate_update(
        self,
        candidates: tuple[HardwareCandidate, ...],
        reason: str,
    ) -> PreflightResult:
        candidate_addresses = tuple(candidate.address for candidate in candidates)
        duplicate_candidates = _duplicates(candidate_addresses)
        if duplicate_candidates:
            return self._fail(
                f"discovery returned duplicate {self.name} addresses: "
                f"{', '.join(duplicate_candidates)}."
            )
        if len(candidate_addresses) < self.min_targets:
            return self._fail(
                f"discovery found {len(candidate_addresses)} {self.name} device(s), "
                f"but at least {self.min_targets} are required. {self.missing_guidance}"
            )
        unreachable = tuple(
            address for address in candidate_addresses if not self._probe_reachable(address)
        )
        if unreachable:
            return self._probe_failed_result(
                f"{reason}, but reachability probes failed for {', '.join(unreachable)}.",
                config_updates={self.config_key: ",".join(candidate_addresses)},
                addresses=unreachable,
            )

        return PreflightResult.ok(
            config_updates={self.config_key: ",".join(candidate_addresses)},
            notes=(f"{reason}; using {self.config_key}={','.join(candidate_addresses)}",),
        )

    def _all_reachable(self, addresses: Sequence[str]) -> bool:
        return all(self._probe_reachable(address) for address in addresses)

    def _probe_reachable(self, address: str) -> bool:
        try:
            return self.probe(address, self.connect_timeout)
        except Exception:
            return False

    def _probe_failed_result(
        self,
        warning: str,
        *,
        config_updates: Mapping[str, str] | None = None,
        addresses: Sequence[str] = (),
    ) -> PreflightResult:
        if self.allow_discovered_but_probe_failed:
            return PreflightResult.ok(
                config_updates=config_updates,
                warnings=(
                    f"{warning} Continuing on discovery evidence only. "
                    f"{self._diagnostic_guidance(addresses)}",
                ),
            )
        return self._fail(f"{warning} {self._diagnostic_guidance(addresses)}")

    def _fail(self, message: str) -> PreflightResult:
        return PreflightResult.fail(f"{self.name} preflight could not resolve hardware. {message}")

    def _diagnostic_guidance(self, addresses: Sequence[str] = ()) -> str:
        guidance = self.diagnostic_guidance or self.missing_guidance
        if not addresses:
            return guidance
        joined = ",".join(addresses)
        return guidance.replace("<ip>", joined).replace("{address}", joined)


def _configured_address(config: GlobalConfig, config_key: str) -> str | None:
    value = getattr(config, config_key)
    if value is None:
        return None
    return str(value)


def _configured_addresses(config: GlobalConfig, config_key: str) -> tuple[str, ...]:
    value = getattr(config, config_key)
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(part.strip() for part in value.split(",") if part.strip())
    return tuple(str(part).strip() for part in value if str(part).strip())


def _is_virtual_value(value: str | None, virtual_values: frozenset[str]) -> bool:
    return value is not None and value.lower() in virtual_values


def _all_virtual_values(values: Sequence[str], virtual_values: frozenset[str]) -> bool:
    return bool(values) and all(value.lower() in virtual_values for value in values)


def _has_mixed_virtual_values(values: Sequence[str], virtual_values: frozenset[str]) -> bool:
    if not values:
        return False
    virtual_count = sum(1 for value in values if value.lower() in virtual_values)
    return 0 < virtual_count < len(values)


def _duplicates(values: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    duplicates: list[str] = []
    for value in values:
        if value in seen and value not in duplicates:
            duplicates.append(value)
        seen.add(value)
    return tuple(duplicates)


def _candidate_list(candidates: Sequence[HardwareCandidate]) -> str:
    return ", ".join(_candidate_label(candidate) for candidate in candidates)


def _candidate_label(candidate: HardwareCandidate) -> str:
    parts = [candidate.address]
    if candidate.identity:
        parts.append(f"id={candidate.identity}")
    if candidate.interface:
        parts.append(f"iface={candidate.interface}")
    if candidate.transport:
        parts.append(f"transport={candidate.transport}")
    return " ".join(parts)


__all__ = [
    "DiscoverHardware",
    "HardwareCandidate",
    "MultiHardwareTargetPreflight",
    "ProbeHardware",
    "SingleHardwareTargetPreflight",
]
