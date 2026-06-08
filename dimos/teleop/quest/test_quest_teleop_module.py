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

import json
from pathlib import Path
import threading
from types import SimpleNamespace
from typing import Any

from dimos.core.global_config import GlobalConfig
from dimos.teleop.quest import quest_teleop_module
from dimos.teleop.quest.quest_extensions import Go2TeleopConfig, Go2TeleopModule
from dimos.teleop.quest.quest_teleop_module import Hand, QuestTeleopConfig, QuestTeleopModule
from dimos.teleop.quest.startup import quest_teleop_lan_ui_preflight


def test_quest_teleop_web_server_uses_module_global_config_listen_host(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    class FakeRobotWebInterface:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(quest_teleop_module, "RobotWebInterface", FakeRobotWebInterface)

    module = QuestTeleopModule.__new__(QuestTeleopModule)
    module.config = QuestTeleopConfig(
        server_port=8443,
        g=GlobalConfig(listen_host="0.0.0.0"),
    )

    module._create_web_server()

    assert captured == {"host": "0.0.0.0", "port": 8443}


def test_quest_teleop_start_server_logs_configured_listen_host(monkeypatch) -> None:
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

    monkeypatch.setattr(quest_teleop_module.threading, "Thread", FakeThread)
    monkeypatch.setattr(quest_teleop_module.logger, "info", messages.append)

    module = QuestTeleopModule.__new__(QuestTeleopModule)
    module.config = QuestTeleopConfig(
        server_port=8443,
        g=GlobalConfig(listen_host="192.168.0.105"),
    )
    module._web_server = FakeWebServer()
    module._web_server_thread = None

    module._start_server()

    assert messages == ["Quest teleop web server started on https://192.168.0.105:8443"]


def test_quest_teleop_lan_ui_preflight_warns_on_loopback() -> None:
    result = quest_teleop_lan_ui_preflight(GlobalConfig(listen_host="localhost"))

    assert result.errors == ()
    assert "Quest teleop is loopback-only" in result.warnings[0]


def test_quest_teleop_lan_ui_preflight_allows_lan_bind() -> None:
    result = quest_teleop_lan_ui_preflight(GlobalConfig(listen_host="0.0.0.0"))

    assert result.errors == ()
    assert result.warnings == ()


def test_quest_teleop_accepts_json_pose_messages() -> None:
    module = QuestTeleopModule.__new__(QuestTeleopModule)
    module._lock = threading.RLock()
    module._current_poses = {Hand.LEFT: None, Hand.RIGHT: None}

    module._on_json_message(
        json.dumps(
            {
                "type": "pose",
                "ts": 1.5,
                "frame_id": "left",
                "position": {"x": 0.1, "y": 1.2, "z": -0.3},
                "orientation": {"x": 0, "y": 0, "z": 0, "w": 1},
            }
        )
    )

    assert module._current_poses[Hand.LEFT] is not None


def test_go2_quest_teleop_accepts_json_joy_and_publishes_cmd_vel() -> None:
    published: list[Any] = []
    module = Go2TeleopModule.__new__(Go2TeleopModule)
    module.config = Go2TeleopConfig()
    module._lock = threading.RLock()
    module._controllers = {Hand.LEFT: None, Hand.RIGHT: None}
    module.cmd_vel = SimpleNamespace(publish=published.append)

    module._on_json_message(
        json.dumps(
            {
                "type": "joy",
                "ts": 1.5,
                "frame_id": "left",
                "axes": [0.25, -0.5, 0.0, 0.0],
                "buttons": [0, 0],
            }
        )
    )
    module._on_json_message(
        json.dumps(
            {
                "type": "joy",
                "ts": 1.6,
                "frame_id": "right",
                "axes": [0.5, 0.0, 0.0, 0.0],
                "buttons": [],
            }
        )
    )

    assert len(published) == 2
    assert published[-1].linear.x == 0.25
    assert published[-1].linear.y == -0.125
    assert published[-1].angular.z == -0.4


def test_quest_teleop_ignores_malformed_json_messages(monkeypatch) -> None:
    warnings: list[str] = []
    module = QuestTeleopModule.__new__(QuestTeleopModule)
    module._lock = threading.RLock()
    module._current_poses = {Hand.LEFT: None, Hand.RIGHT: None}

    monkeypatch.setattr(quest_teleop_module.logger, "warning", warnings.append)

    module._on_json_message("{not-json")
    module._on_json_message(json.dumps({"type": "pose", "frame_id": "middle"}))

    assert len(warnings) == 2
    assert all(
        message.startswith("Ignoring malformed Quest teleop JSON message") for message in warnings
    )
    assert module._current_poses == {Hand.LEFT: None, Hand.RIGHT: None}


def test_quest_teleop_browser_ui_does_not_import_cdn_modules() -> None:
    source = (Path(__file__).parent / "web" / "static" / "teleop.js").read_text()

    assert "https://esm.sh" not in source
    assert "@dimos/msgs" not in source
