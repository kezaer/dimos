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

from dimos.core.coordination.preflight import run_preflights
from dimos.core.global_config import GlobalConfig
from dimos.core.transport import JpegLcmTransport, LCMTransport
from dimos.hardware.preflight import HardwareCandidate
from dimos.msgs.geometry_msgs.Twist import Twist
from dimos.msgs.sensor_msgs.Image import Image
from dimos.robot.unitree.go2 import startup as go2_startup
from dimos.teleop.phone.blueprints import teleop_phone, teleop_phone_go2, teleop_phone_go2_fleet
from dimos.teleop.phone.startup import _is_loopback_host, phone_teleop_lan_ui_preflight


def test_teleop_phone_go2_routes_browser_keyboard_to_go2_cmd_vel() -> None:
    transport = teleop_phone_go2.transport_map[("tele_cmd_vel", Twist)]

    assert type(transport) is LCMTransport
    assert transport.topic.topic == "/cmd_vel"
    assert transport.topic.lcm_type is Twist


def test_teleop_phone_go2_exposes_camera_to_rerun_over_jpeg_lcm() -> None:
    transport = teleop_phone_go2.transport_map[("color_image", Image)]

    assert isinstance(transport, JpegLcmTransport)
    assert transport.topic.topic == "/color_image"
    assert transport.topic.lcm_type is Image


def test_phone_teleop_lan_ui_preflight_warns_on_default_loopback_host() -> None:
    config = GlobalConfig(listen_host="127.0.0.1")

    result = phone_teleop_lan_ui_preflight(config)

    assert result.config_updates == {}
    assert "loopback-only" in result.warnings[0]
    assert "dimos --listen-host 0.0.0.0 run <blueprint>" in result.warnings[0]
    assert config.listen_host == "127.0.0.1"


def test_phone_teleop_lan_ui_preflight_detects_loopback_aliases() -> None:
    assert _is_loopback_host("127.0.0.1") is True
    assert _is_loopback_host("localhost") is True
    assert _is_loopback_host("::1") is True
    assert _is_loopback_host("fe80::1%en0") is False
    assert _is_loopback_host("0.0.0.0") is False
    assert _is_loopback_host("192.168.0.105") is False


def test_phone_teleop_lan_ui_preflight_keeps_explicit_host() -> None:
    config = GlobalConfig(listen_host="192.168.0.105")

    result = phone_teleop_lan_ui_preflight(config)

    assert result.errors == ()
    assert result.config_updates == {}
    assert config.listen_host == "192.168.0.105"


def test_phone_teleop_go2_blueprints_expose_lan_ui_but_plain_phone_does_not() -> None:
    assert phone_teleop_lan_ui_preflight not in teleop_phone.preflight_checks
    assert phone_teleop_lan_ui_preflight in teleop_phone_go2.preflight_checks
    assert phone_teleop_lan_ui_preflight in teleop_phone_go2_fleet.preflight_checks


def test_phone_teleop_go2_preflights_preserve_loopback_host(capsys) -> None:
    config = GlobalConfig(listen_host="127.0.0.1", replay=True)

    run_preflights(teleop_phone_go2, config)

    assert config.listen_host == "127.0.0.1"
    assert "loopback-only" in capsys.readouterr().err


def test_phone_teleop_go2_preflights_fill_missing_robot_ip_without_auto_exposing_lan_ui(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        go2_startup,
        "discover_go2_candidates",
        lambda timeout: [
            HardwareCandidate(address="192.168.0.117", identity="SN123", interface="en0")
        ],
    )
    monkeypatch.setattr(
        go2_startup,
        "probe_go2_signal",
        lambda address, timeout: address == "192.168.0.117",
    )
    config = GlobalConfig(robot_ip=None, listen_host="127.0.0.1")

    run_preflights(teleop_phone_go2, config)

    assert config.robot_ip == "192.168.0.117"
    assert config.listen_host == "127.0.0.1"
    stderr = capsys.readouterr().err
    assert "selected the only discovered Go2; using robot_ip=192.168.0.117" in stderr
    assert "loopback-only" in stderr
