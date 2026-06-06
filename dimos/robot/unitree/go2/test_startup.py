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

from pydantic import ValidationError
import pytest

from dimos.core.coordination.preflight import run_preflights
from dimos.core.global_config import GlobalConfig, global_config
from dimos.hardware.preflight import HardwareCandidate
from dimos.robot.unitree.go2 import startup as go2_startup
from dimos.robot.unitree.go2.fleet_connection import FleetConnectionConfig


@pytest.fixture(autouse=True)
def restore_global_config():
    snapshot = global_config.model_dump()
    yield
    global_config.update(**snapshot)


def test_go2_startup_preflight_sets_single_discovered_ip(monkeypatch) -> None:
    config = GlobalConfig(robot_ip=None, replay=False, simulation="")
    monkeypatch.setattr(
        go2_startup,
        "discover_go2_candidates",
        lambda timeout: [
            HardwareCandidate(identity="SN123", address="192.168.0.117", interface="en0")
        ],
    )
    monkeypatch.setattr(
        go2_startup,
        "probe_go2_signal",
        lambda address, timeout: True,
    )

    result = go2_startup.go2_startup_preflight(config)

    assert result.errors == ()
    assert result.config_updates == {"robot_ip": "192.168.0.117"}
    assert "selected the only discovered Go2" in result.notes[0]
    assert config.robot_ip is None


def test_go2_startup_preflight_rejects_one_stale_configured_ip(monkeypatch) -> None:
    config = GlobalConfig(robot_ip="192.168.0.200", replay=False, simulation="")
    monkeypatch.setattr(
        go2_startup,
        "discover_go2_candidates",
        lambda timeout: [
            HardwareCandidate(identity="SN123", address="192.168.0.117", interface="en0")
        ],
    )
    monkeypatch.setattr(
        go2_startup,
        "probe_go2_signal",
        lambda address, timeout: address == "192.168.0.117",
    )

    result = go2_startup.go2_startup_preflight(config)

    assert len(result.errors) == 1
    assert result.config_updates == {}
    assert "Set robot_ip explicitly to use a different device" in result.errors[0]
    assert config.robot_ip == "192.168.0.200"


def test_go2_startup_preflight_fails_on_discovery_only(monkeypatch) -> None:
    config = GlobalConfig(robot_ip=None, replay=False, simulation="")
    monkeypatch.setattr(
        go2_startup,
        "discover_go2_candidates",
        lambda timeout: [
            HardwareCandidate(identity="SN123", address="192.168.0.117", interface="en0")
        ],
    )
    monkeypatch.setattr(
        go2_startup,
        "probe_go2_signal",
        lambda address, timeout: False,
    )

    result = go2_startup.go2_startup_preflight(config)

    assert len(result.errors) == 1
    assert result.config_updates == {}
    assert "reachability probe" in result.errors[0]
    assert "dimos go2tool doctor --robot-ip 192.168.0.117" in result.errors[0]
    assert "--check-image" not in result.errors[0]


def test_go2_startup_preflight_requires_selection_when_multiple_go2s_are_found(
    monkeypatch,
) -> None:
    config = GlobalConfig(robot_ip=None, replay=False, simulation="")
    monkeypatch.setattr(
        go2_startup,
        "discover_go2_candidates",
        lambda timeout: [
            HardwareCandidate(identity="SN123", address="192.168.0.117", interface="en0"),
            HardwareCandidate(identity="SN456", address="192.168.0.118", interface="en0"),
        ],
    )

    result = go2_startup.go2_startup_preflight(config)

    assert len(result.errors) == 1
    assert "multiple Go2 devices discovered" in result.errors[0]
    assert result.config_updates == {}
    assert config.robot_ip is None


@pytest.mark.parametrize(
    "config",
    [
        GlobalConfig(robot_ip=None, replay=True, simulation=""),
        GlobalConfig(robot_ip=None, replay=False, simulation="mujoco"),
        GlobalConfig(robot_ip=None, replay=False, simulation="dimsim"),
        GlobalConfig(robot_ip="mock", replay=False, simulation=""),
        GlobalConfig(robot_ip="fake", replay=False, simulation=""),
        GlobalConfig(robot_ip="replay", replay=False, simulation=""),
        GlobalConfig(robot_ip="mujoco", replay=False, simulation=""),
    ],
)
def test_go2_startup_preflight_skips_virtual_modes(monkeypatch, config: GlobalConfig) -> None:
    monkeypatch.setattr(
        go2_startup,
        "discover_go2_candidates",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("should not check robot in virtual mode")
        ),
    )
    monkeypatch.setattr(
        go2_startup,
        "probe_go2_signal",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("should not probe robot in virtual mode")
        ),
    )

    result = go2_startup.go2_startup_preflight(config)

    assert result.errors == ()
    assert result.config_updates == {}


def test_go2_preflight_updates_apply_through_core_runner(monkeypatch, capsys) -> None:
    config = GlobalConfig(robot_ip=None, replay=False, simulation="")
    monkeypatch.setattr(
        go2_startup,
        "discover_go2_candidates",
        lambda timeout: [
            HardwareCandidate(identity="SN123", address="192.168.0.117", interface="en0")
        ],
    )
    monkeypatch.setattr(
        go2_startup,
        "probe_go2_signal",
        lambda address, timeout: True,
    )

    from dimos.robot.unitree.go2.blueprints.basic.unitree_go2_basic import unitree_go2_basic

    run_preflights(unitree_go2_basic, config)

    assert config.robot_ip == "192.168.0.117"
    assert "selected the only discovered Go2" in capsys.readouterr().err


def test_go2_startup_preflight_skips_when_go2_connection_is_disabled(monkeypatch) -> None:
    from dimos.robot.unitree.go2.blueprints.basic.unitree_go2_basic import unitree_go2_basic
    from dimos.robot.unitree.go2.connection import GO2Connection

    monkeypatch.setattr(
        go2_startup,
        "discover_go2_candidates",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("disabled Go2 connection should not discover hardware")
        ),
    )
    monkeypatch.setattr(
        go2_startup,
        "probe_go2_signal",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("disabled Go2 connection should not probe hardware")
        ),
    )
    config = GlobalConfig(robot_ip=None, replay=False, simulation="")

    run_preflights(unitree_go2_basic.disabled_modules(GO2Connection), config)

    assert config.robot_ip is None


def test_go2_fleet_startup_preflight_requires_explicit_ips_when_fleet_discovered(
    monkeypatch,
) -> None:
    config = GlobalConfig(robot_ips=None, replay=False, simulation="")
    monkeypatch.setattr(
        go2_startup,
        "discover_go2_candidates",
        lambda timeout: [
            HardwareCandidate(identity="SN123", address="192.168.0.117", interface="en0"),
            HardwareCandidate(identity="SN456", address="192.168.0.118", interface="en0"),
        ],
    )
    monkeypatch.setattr(
        go2_startup,
        "probe_go2_signal",
        lambda address, timeout: True,
    )

    result = go2_startup.go2_fleet_startup_preflight(config)

    assert len(result.errors) == 1
    assert result.config_updates == {}
    assert "Set robot_ips explicitly to the intended fleet" in result.errors[0]
    assert config.robot_ips is None


def test_go2_fleet_startup_preflight_keeps_reachable_configured_ips(monkeypatch) -> None:
    config = GlobalConfig(robot_ips="192.168.0.117,192.168.0.118", replay=False, simulation="")
    monkeypatch.setattr(
        go2_startup,
        "discover_go2_candidates",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("reachable configured fleet should not discover hardware")
        ),
    )
    monkeypatch.setattr(
        go2_startup,
        "probe_go2_signal",
        lambda address, timeout: address in {"192.168.0.117", "192.168.0.118"},
    )

    result = go2_startup.go2_fleet_startup_preflight(config)

    assert result.errors == ()
    assert result.config_updates == {}


def test_go2_fleet_startup_preflight_rejects_discovered_replacement_fleet(
    monkeypatch,
) -> None:
    config = GlobalConfig(robot_ips="192.168.0.200,192.168.0.201", replay=False, simulation="")
    monkeypatch.setattr(
        go2_startup,
        "discover_go2_candidates",
        lambda timeout: [
            HardwareCandidate(identity="SN123", address="192.168.0.117", interface="en0"),
            HardwareCandidate(identity="SN456", address="192.168.0.118", interface="en0"),
        ],
    )
    monkeypatch.setattr(
        go2_startup,
        "probe_go2_signal",
        lambda address, timeout: address in {"192.168.0.117", "192.168.0.118"},
    )

    result = go2_startup.go2_fleet_startup_preflight(config)

    assert len(result.errors) == 1
    assert result.config_updates == {}
    assert "Set robot_ips explicitly to the intended fleet" in result.errors[0]
    assert config.robot_ips == "192.168.0.200,192.168.0.201"


def test_go2_fleet_startup_preflight_fails_on_unverified_configured_ip(monkeypatch) -> None:
    config = GlobalConfig(robot_ips="192.168.0.117,192.168.0.118", replay=False, simulation="")
    monkeypatch.setattr(
        go2_startup,
        "discover_go2_candidates",
        lambda timeout: [
            HardwareCandidate(identity="SN123", address="192.168.0.117", interface="en0"),
            HardwareCandidate(identity="SN456", address="192.168.0.118", interface="en0"),
        ],
    )
    monkeypatch.setattr(
        go2_startup,
        "probe_go2_signal",
        lambda address, timeout: address == "192.168.0.117",
    )

    result = go2_startup.go2_fleet_startup_preflight(config)

    assert len(result.errors) == 1
    assert result.config_updates == {}
    assert "reachability probes failed for 192.168.0.118" in result.errors[0]
    assert "dimos go2tool doctor --robot-ip 192.168.0.118" in result.errors[0]
    assert "--check-image" not in result.errors[0]


def test_go2_fleet_startup_preflight_skips_virtual_modes(monkeypatch) -> None:
    config = GlobalConfig(robot_ips="mock,mock", replay=False, simulation="")
    monkeypatch.setattr(
        go2_startup,
        "discover_go2_candidates",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("virtual fleet should not discover hardware")
        ),
    )
    monkeypatch.setattr(
        go2_startup,
        "probe_go2_signal",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("virtual fleet should not probe hardware")
        ),
    )

    result = go2_startup.go2_fleet_startup_preflight(config)

    assert result.errors == ()
    assert result.config_updates == {}


@pytest.mark.parametrize(
    ("config", "expected_ips", "expected_ip"),
    [
        (GlobalConfig(replay=True), ["replay"], "replay"),
        (GlobalConfig(simulation="mujoco"), ["mujoco"], "mujoco"),
        (GlobalConfig(simulation="dimsim"), ["dimsim"], "dimsim"),
        (GlobalConfig(robot_ip="192.168.0.117"), ["192.168.0.117"], "192.168.0.117"),
        (
            GlobalConfig(robot_ips="192.168.0.117, 192.168.0.118"),
            ["192.168.0.117", "192.168.0.118"],
            "192.168.0.117",
        ),
    ],
)
def test_go2_fleet_connection_config_defaults_ips_from_mode_or_config(
    config: GlobalConfig,
    expected_ips: list[str],
    expected_ip: str,
) -> None:
    fleet_config = FleetConnectionConfig(g=config)

    assert fleet_config.ips == expected_ips
    assert fleet_config.ip == expected_ip


def test_go2_fleet_connection_config_fails_closed_without_real_robot_ips() -> None:
    with pytest.raises(ValidationError, match="robot_ips must be set"):
        FleetConnectionConfig(
            g=GlobalConfig(robot_ip=None, robot_ips=None, replay=False, simulation="")
        )


def test_go2_fleet_startup_preflight_skips_when_fleet_connection_is_disabled(
    monkeypatch,
) -> None:
    from dimos.robot.unitree.go2.blueprints.basic.unitree_go2_fleet import unitree_go2_fleet
    from dimos.robot.unitree.go2.fleet_connection import Go2FleetConnection

    monkeypatch.setattr(
        go2_startup,
        "discover_go2_candidates",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("disabled Go2 fleet connection should not discover hardware")
        ),
    )
    monkeypatch.setattr(
        go2_startup,
        "probe_go2_signal",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("disabled Go2 fleet connection should not probe hardware")
        ),
    )
    config = GlobalConfig(robot_ips=None, replay=False, simulation="")

    run_preflights(unitree_go2_fleet.disabled_modules(Go2FleetConnection), config)

    assert config.robot_ips is None


def test_go2_preflights_are_wired_into_blueprints() -> None:
    from dimos.robot.get_all_blueprints import get_module_by_name
    from dimos.robot.unitree.go2.blueprints.basic.unitree_go2_basic import unitree_go2_basic
    from dimos.robot.unitree.go2.blueprints.basic.unitree_go2_coordinator import (
        unitree_go2_coordinator,
    )
    from dimos.robot.unitree.go2.blueprints.basic.unitree_go2_fleet import unitree_go2_fleet
    from dimos.robot.unitree.go2.blueprints.smart.unitree_go2 import unitree_go2

    assert go2_startup.go2_startup_preflight in unitree_go2_basic.preflight_checks
    assert go2_startup.go2_startup_preflight in unitree_go2_coordinator.preflight_checks
    assert go2_startup.go2_fleet_startup_preflight in unitree_go2_fleet.preflight_checks
    assert go2_startup.go2_startup_preflight in unitree_go2.preflight_checks
    assert (
        go2_startup.go2_startup_preflight in get_module_by_name("go2-connection").preflight_checks
    )
    assert (
        go2_startup.go2_fleet_startup_preflight
        in get_module_by_name("go2-fleet-connection").preflight_checks
    )
