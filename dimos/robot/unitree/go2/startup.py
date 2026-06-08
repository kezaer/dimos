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

"""Startup preflight helpers for real Unitree Go2 blueprints."""

from __future__ import annotations

from dimos.core.coordination.preflight import PreflightResult, owned_preflight
from dimos.core.global_config import GlobalConfig
from dimos.hardware.preflight import MultiHardwareTargetPreflight, SingleHardwareTargetPreflight
from dimos.robot.unitree.go2.connectivity import (
    discover_go2_candidates,
    probe_go2_signal,
)

_CONNECT_TIMEOUT_SEC = 0.8
_DISCOVERY_TIMEOUT_SEC = 1.5
_VIRTUAL_IPS = frozenset({"fake", "mock", "mujoco", "replay"})


def _go2_startup_preflight(config: GlobalConfig) -> PreflightResult:
    """Resolve and validate the Go2 IP before real Go2 modules are deployed."""
    return _go2_single_target_preflight()(config)


def _go2_fleet_startup_preflight(config: GlobalConfig) -> PreflightResult:
    """Resolve and validate Go2 fleet IPs before real fleet modules are deployed."""
    return _go2_multi_target_preflight()(config)


go2_startup_preflight = owned_preflight(
    _go2_startup_preflight,
    "dimos.robot.unitree.go2.connection.GO2Connection",
    name="go2_startup_preflight",
)
go2_fleet_startup_preflight = owned_preflight(
    _go2_fleet_startup_preflight,
    "dimos.robot.unitree.go2.fleet_connection.Go2FleetConnection",
    name="go2_fleet_startup_preflight",
)


def _go2_single_target_preflight() -> SingleHardwareTargetPreflight:
    return SingleHardwareTargetPreflight(
        name="Go2",
        config_key="robot_ip",
        discover=discover_go2_candidates,
        probe=probe_go2_signal,
        virtual_values=_VIRTUAL_IPS,
        is_virtual_mode=lambda config: config.unitree_connection_type.lower() != "webrtc",
        discovery_timeout=_DISCOVERY_TIMEOUT_SEC,
        connect_timeout=_CONNECT_TIMEOUT_SEC,
        allow_discovered_but_probe_failed=False,
        missing_guidance="Pass --robot-ip <ip> or connect to the same local Wi-Fi as the Go2.",
        diagnostic_guidance=(
            "Run `dimos go2tool doctor --robot-ip <ip>` for a detailed connection report."
        ),
    )


def _go2_multi_target_preflight() -> MultiHardwareTargetPreflight:
    return MultiHardwareTargetPreflight(
        name="Go2 fleet",
        config_key="robot_ips",
        discover=discover_go2_candidates,
        probe=probe_go2_signal,
        virtual_values=_VIRTUAL_IPS,
        is_virtual_mode=lambda config: config.unitree_connection_type.lower() != "webrtc",
        discovery_timeout=_DISCOVERY_TIMEOUT_SEC,
        connect_timeout=_CONNECT_TIMEOUT_SEC,
        allow_discovered_but_probe_failed=False,
        allow_discovered_selection=False,
        missing_guidance=(
            "Pass --robot-ips <ip1,ip2,...> or connect to the same local Wi-Fi as the Go2 fleet."
        ),
        diagnostic_guidance=(
            "Run `dimos go2tool doctor --robot-ip <ip>` for each fleet IP that failed validation."
        ),
    )


__all__ = ["go2_fleet_startup_preflight", "go2_startup_preflight"]
