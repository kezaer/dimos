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

import json
from pathlib import Path
import threading
from typing import Any

from dimos.core.global_config import GlobalConfig
from dimos.teleop.phone import phone_teleop_module
from dimos.teleop.phone.phone_teleop_module import PhoneTeleopConfig, PhoneTeleopModule


def test_phone_teleop_web_server_uses_module_global_config_listen_host(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    class FakeRobotWebInterface:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(phone_teleop_module, "RobotWebInterface", FakeRobotWebInterface)

    module = PhoneTeleopModule.__new__(PhoneTeleopModule)
    module.config = PhoneTeleopConfig(
        server_port=8444,
        g=GlobalConfig(listen_host="0.0.0.0"),
    )

    module._create_web_server()

    assert captured == {"host": "0.0.0.0", "port": 8444}


def test_phone_teleop_start_server_logs_configured_listen_host(monkeypatch) -> None:
    messages: list[str] = []

    class FakeThread:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def start(self) -> None:
            pass

        def is_alive(self) -> bool:
            return False

    class FakeWebServer:
        def run(self, **kwargs: Any) -> None:
            pass

    monkeypatch.setattr(phone_teleop_module.threading, "Thread", FakeThread)
    monkeypatch.setattr(phone_teleop_module.logger, "info", messages.append)

    module = PhoneTeleopModule.__new__(PhoneTeleopModule)
    module.config = PhoneTeleopConfig(
        server_port=8444,
        g=GlobalConfig(listen_host="192.168.0.105"),
    )
    module._web_server = FakeWebServer()
    module._web_server_thread = None

    module._start_server()

    assert messages == ["Phone teleop web server started on https://192.168.0.105:8444"]


def test_phone_teleop_accepts_json_sensor_and_button_messages() -> None:
    module = PhoneTeleopModule.__new__(PhoneTeleopModule)
    module._lock = threading.RLock()
    module._current_sensors = None
    module._teleop_button = False

    module._on_json_message(
        json.dumps(
            {
                "type": "phone_sensors",
                "ts": 1.5,
                "frame_id": "phone",
                "linear": {"x": 1, "y": 2, "z": 3},
                "angular": {"x": 4, "y": 5, "z": 6},
            }
        )
    )
    module._on_json_message(json.dumps({"type": "phone_button", "data": True}))

    assert module._current_sensors.frame_id == "phone"
    assert module._current_sensors.linear.x == 1
    assert module._current_sensors.angular.z == 6
    assert module._teleop_button is True


def test_phone_teleop_ignores_malformed_json_messages(monkeypatch) -> None:
    warnings: list[str] = []
    module = PhoneTeleopModule.__new__(PhoneTeleopModule)
    module._lock = threading.RLock()
    module._current_sensors = None
    module._teleop_button = True

    monkeypatch.setattr(phone_teleop_module.logger, "warning", warnings.append)

    module._on_json_message("{not-json")
    module._on_json_message(json.dumps({"type": "phone_button", "data": "false"}))
    assert module._teleop_button is False
    module._on_json_message(json.dumps({"type": "phone_button", "data": 1}))

    assert warnings[0].startswith("Ignoring malformed phone teleop JSON message")
    assert module._teleop_button is True


def test_phone_teleop_browser_ui_does_not_import_cdn_modules() -> None:
    source = (Path(__file__).parent / "web" / "static" / "index.html").read_text()

    assert "https://esm.sh" not in source
    assert "@dimos/msgs" not in source
