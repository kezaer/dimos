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

from __future__ import annotations

from collections.abc import Sequence

from dimos.core.global_config import GlobalConfig
from dimos.hardware.preflight import (
    HardwareCandidate,
    MultiHardwareTargetPreflight,
    SingleHardwareTargetPreflight,
)


def _preflight(
    *,
    candidates: Sequence[HardwareCandidate],
    reachable: set[str] | None = None,
    virtual: bool = False,
    allow_discovered_but_probe_failed: bool = False,
    allow_configured_replacement: bool = False,
) -> SingleHardwareTargetPreflight:
    reachable = reachable or set()

    return SingleHardwareTargetPreflight(
        name="TestBot",
        config_key="robot_ip",
        discover=lambda timeout: candidates,
        probe=lambda address, timeout: address in reachable,
        virtual_values=frozenset({"mock"}),
        is_virtual_mode=lambda config: virtual,
        allow_discovered_but_probe_failed=allow_discovered_but_probe_failed,
        allow_configured_replacement=allow_configured_replacement,
        missing_guidance="Set --robot-ip.",
        diagnostic_guidance="Run diagnostics.",
    )


def _fleet_preflight(
    *,
    candidates: Sequence[HardwareCandidate],
    reachable: set[str] | None = None,
    virtual: bool = False,
    allow_discovered_selection: bool = True,
) -> MultiHardwareTargetPreflight:
    reachable = reachable or set()
    return MultiHardwareTargetPreflight(
        name="TestBot fleet",
        config_key="robot_ips",
        discover=lambda timeout: candidates,
        probe=lambda address, timeout: address in reachable,
        virtual_values=frozenset({"mock"}),
        is_virtual_mode=lambda config: virtual,
        allow_discovered_selection=allow_discovered_selection,
        missing_guidance="Set --robot-ips.",
        diagnostic_guidance="Run fleet diagnostics for {address}.",
    )


def test_single_hardware_preflight_uses_reachable_configured_address_without_discovery() -> None:
    discovered = False

    def discover(timeout: float) -> Sequence[HardwareCandidate]:
        nonlocal discovered
        discovered = True
        return []

    preflight = SingleHardwareTargetPreflight(
        name="TestBot",
        config_key="robot_ip",
        discover=discover,
        probe=lambda address, timeout: address == "192.168.0.117",
        virtual_values=frozenset(),
        is_virtual_mode=lambda config: False,
    )

    result = preflight(GlobalConfig(robot_ip="192.168.0.117"))

    assert result.errors == ()
    assert result.config_updates == {}
    assert discovered is False


def test_single_hardware_preflight_selects_one_discovered_candidate() -> None:
    preflight = _preflight(
        candidates=[HardwareCandidate(address="192.168.0.117", identity="SN123")],
        reachable={"192.168.0.117"},
    )

    result = preflight(GlobalConfig(robot_ip=None))

    assert result.errors == ()
    assert result.config_updates == {"robot_ip": "192.168.0.117"}
    assert "selected the only discovered TestBot" in result.notes[0]


def test_single_hardware_preflight_replaces_stale_address_when_explicitly_allowed() -> None:
    preflight = _preflight(
        candidates=[HardwareCandidate(address="192.168.0.117", identity="SN123")],
        reachable={"192.168.0.117"},
        allow_configured_replacement=True,
    )

    result = preflight(GlobalConfig(robot_ip="192.168.0.200"))

    assert result.errors == ()
    assert result.config_updates == {"robot_ip": "192.168.0.117"}
    assert "replaced unreachable configured robot_ip" in result.notes[0]


def test_single_hardware_preflight_rejects_stale_retarget_by_default() -> None:
    preflight = _preflight(
        candidates=[HardwareCandidate(address="192.168.0.117", identity="SN123")],
        reachable={"192.168.0.117"},
    )

    result = preflight(GlobalConfig(robot_ip="192.168.0.200"))

    assert len(result.errors) == 1
    assert result.config_updates == {}
    assert "Set robot_ip explicitly to use a different device" in result.errors[0]


def test_single_hardware_preflight_fails_when_discovered_address_fails_probe_by_default() -> None:
    preflight = _preflight(
        candidates=[HardwareCandidate(address="192.168.0.117", identity="SN123")],
        reachable=set(),
    )

    result = preflight(GlobalConfig(robot_ip=None))

    assert len(result.errors) == 1
    assert result.config_updates == {}
    assert "reachability probe" in result.errors[0]


def test_single_hardware_preflight_can_warn_when_adapter_allows_probe_failure() -> None:
    preflight = SingleHardwareTargetPreflight(
        name="TestBot",
        config_key="robot_ip",
        discover=lambda timeout: [HardwareCandidate(address="192.168.0.117", identity="SN123")],
        probe=lambda address, timeout: False,
        virtual_values=frozenset({"mock"}),
        is_virtual_mode=lambda config: False,
        allow_discovered_but_probe_failed=True,
        diagnostic_guidance="Run diagnostics for <ip>.",
    )

    result = preflight(GlobalConfig(robot_ip=None))

    assert result.errors == ()
    assert result.config_updates == {"robot_ip": "192.168.0.117"}
    assert "reachability probe" in result.warnings[0]
    assert "Run diagnostics for 192.168.0.117." in result.warnings[0]


def test_single_hardware_preflight_can_warn_when_adapter_allows_configured_probe_failure() -> None:
    preflight = _preflight(
        candidates=[HardwareCandidate(address="192.168.0.117", identity="SN123")],
        reachable=set(),
        allow_discovered_but_probe_failed=True,
    )

    result = preflight(GlobalConfig(robot_ip="192.168.0.117"))

    assert result.errors == ()
    assert result.config_updates == {}
    assert "reachability probe" in result.warnings[0]


def test_single_hardware_preflight_fails_without_candidates() -> None:
    result = _preflight(candidates=[])(GlobalConfig(robot_ip=None))

    assert len(result.errors) == 1
    assert "discovery found no TestBot" in result.errors[0]


def test_single_hardware_preflight_fails_on_ambiguous_candidates() -> None:
    result = _preflight(
        candidates=[
            HardwareCandidate(address="192.168.0.117", identity="SN123"),
            HardwareCandidate(address="192.168.0.118", identity="SN456"),
        ],
    )(GlobalConfig(robot_ip=None))

    assert len(result.errors) == 1
    assert "multiple TestBot devices discovered" in result.errors[0]
    assert result.config_updates == {}


def test_single_hardware_preflight_skips_virtual_modes_and_values() -> None:
    assert _preflight(candidates=[], virtual=True)(GlobalConfig(robot_ip=None)).errors == ()
    assert _preflight(candidates=[])(GlobalConfig(robot_ip="mock")).errors == ()


def test_multi_hardware_preflight_uses_reachable_configured_addresses_without_discovery() -> None:
    discovered = False

    def discover(timeout: float) -> Sequence[HardwareCandidate]:
        nonlocal discovered
        discovered = True
        return []

    preflight = MultiHardwareTargetPreflight(
        name="TestBot fleet",
        config_key="robot_ips",
        discover=discover,
        probe=lambda address, timeout: address in {"192.168.0.117", "192.168.0.118"},
        virtual_values=frozenset(),
        is_virtual_mode=lambda config: False,
    )

    result = preflight(GlobalConfig(robot_ips="192.168.0.117,192.168.0.118"))

    assert result.errors == ()
    assert result.config_updates == {}
    assert discovered is False


def test_multi_hardware_preflight_selects_all_reachable_discovered_candidates() -> None:
    result = _fleet_preflight(
        candidates=[
            HardwareCandidate(address="192.168.0.117", identity="SN123"),
            HardwareCandidate(address="192.168.0.118", identity="SN456"),
        ],
        reachable={"192.168.0.117", "192.168.0.118"},
    )(GlobalConfig(robot_ips=None))

    assert result.errors == ()
    assert result.config_updates == {"robot_ips": "192.168.0.117,192.168.0.118"}
    assert "selected 2 discovered TestBot fleet" in result.notes[0]


def test_multi_hardware_preflight_can_require_explicit_selection_for_missing_fleet() -> None:
    result = _fleet_preflight(
        candidates=[
            HardwareCandidate(address="192.168.0.117", identity="SN123"),
            HardwareCandidate(address="192.168.0.118", identity="SN456"),
        ],
        reachable={"192.168.0.117", "192.168.0.118"},
        allow_discovered_selection=False,
    )(GlobalConfig(robot_ips=None))

    assert len(result.errors) == 1
    assert result.config_updates == {}
    assert "Set robot_ips explicitly to the intended fleet" in result.errors[0]


def test_multi_hardware_preflight_requires_explicit_selection_by_default() -> None:
    preflight = MultiHardwareTargetPreflight(
        name="TestBot fleet",
        config_key="robot_ips",
        discover=lambda timeout: [HardwareCandidate(address="192.168.0.117")],
        probe=lambda address, timeout: True,
        virtual_values=frozenset({"mock"}),
        is_virtual_mode=lambda config: False,
    )

    result = preflight(GlobalConfig(robot_ips=None))

    assert len(result.errors) == 1
    assert result.config_updates == {}
    assert "Set robot_ips explicitly to the intended fleet" in result.errors[0]


def test_multi_hardware_preflight_replaces_stale_addresses_when_count_matches() -> None:
    result = _fleet_preflight(
        candidates=[
            HardwareCandidate(address="192.168.0.117", identity="SN123"),
            HardwareCandidate(address="192.168.0.118", identity="SN456"),
        ],
        reachable={"192.168.0.117", "192.168.0.118"},
    )(GlobalConfig(robot_ips="192.168.0.200,192.168.0.201"))

    assert result.errors == ()
    assert result.config_updates == {"robot_ips": "192.168.0.117,192.168.0.118"}
    assert "replaced unreachable configured robot_ips" in result.notes[0]


def test_multi_hardware_preflight_can_reject_discovered_replacement_fleet() -> None:
    result = _fleet_preflight(
        candidates=[
            HardwareCandidate(address="192.168.0.117", identity="SN123"),
            HardwareCandidate(address="192.168.0.118", identity="SN456"),
        ],
        reachable={"192.168.0.117", "192.168.0.118"},
        allow_discovered_selection=False,
    )(GlobalConfig(robot_ips="192.168.0.200,192.168.0.201"))

    assert len(result.errors) == 1
    assert result.config_updates == {}
    assert "Set robot_ips explicitly to the intended fleet" in result.errors[0]


def test_multi_hardware_preflight_fails_when_discovered_candidate_fails_probe() -> None:
    result = _fleet_preflight(
        candidates=[
            HardwareCandidate(address="192.168.0.117", identity="SN123"),
            HardwareCandidate(address="192.168.0.118", identity="SN456"),
        ],
        reachable={"192.168.0.117"},
    )(GlobalConfig(robot_ips=None))

    assert len(result.errors) == 1
    assert result.config_updates == {}
    assert "reachability probes failed for 192.168.0.118" in result.errors[0]
    assert "Run fleet diagnostics for 192.168.0.118." in result.errors[0]


def test_multi_hardware_preflight_fails_when_repair_candidate_count_differs() -> None:
    result = _fleet_preflight(
        candidates=[
            HardwareCandidate(address="192.168.0.117", identity="SN123"),
            HardwareCandidate(address="192.168.0.118", identity="SN456"),
        ],
        reachable={"192.168.0.117", "192.168.0.118"},
    )(GlobalConfig(robot_ips="192.168.0.200"))

    assert len(result.errors) == 1
    assert result.config_updates == {}
    assert "Set robot_ips explicitly to the intended fleet" in result.errors[0]


def test_multi_hardware_preflight_rejects_duplicate_configured_addresses() -> None:
    result = _fleet_preflight(candidates=[], reachable=set())(
        GlobalConfig(robot_ips="192.168.0.117,192.168.0.117")
    )

    assert len(result.errors) == 1
    assert "duplicate addresses: 192.168.0.117" in result.errors[0]


def test_multi_hardware_preflight_skips_virtual_modes_and_values() -> None:
    assert _fleet_preflight(candidates=[], virtual=True)(GlobalConfig(robot_ips=None)).errors == ()
    assert _fleet_preflight(candidates=[])(GlobalConfig(robot_ips="mock,mock")).errors == ()


def test_multi_hardware_preflight_rejects_mixed_virtual_and_real_addresses() -> None:
    result = _fleet_preflight(candidates=[])(GlobalConfig(robot_ips="mock,192.168.0.117"))

    assert len(result.errors) == 1
    assert "mixes virtual and real addresses" in result.errors[0]
