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

import requests

from dimos.robot.unitree.go2 import connectivity
from dimos.robot.unitree.go2.lan_discovery import Go2Device


class _Response:
    def __init__(self, ok: bool) -> None:
        self.ok = ok


def test_probe_go2_signal_returns_true_for_ok_response(monkeypatch) -> None:
    calls: list[tuple[str, float]] = []

    def fake_get(url: str, timeout: float) -> _Response:
        calls.append((url, timeout))
        return _Response(ok=True)

    monkeypatch.setattr(connectivity.requests, "get", fake_get)

    assert connectivity.probe_go2_signal("192.168.0.117", timeout=0.25, port=9991) is True
    assert calls == [("http://192.168.0.117:9991/con_notify", 0.25)]


def test_probe_go2_signal_returns_false_for_non_ok_response(monkeypatch) -> None:
    monkeypatch.setattr(
        connectivity.requests,
        "get",
        lambda url, timeout: _Response(ok=False),
    )

    assert connectivity.probe_go2_signal("192.168.0.117", timeout=0.25) is False


def test_probe_go2_signal_returns_false_for_request_errors(monkeypatch) -> None:
    def fail_get(url: str, timeout: float) -> _Response:
        raise requests.Timeout("timed out")

    monkeypatch.setattr(connectivity.requests, "get", fail_get)

    assert connectivity.probe_go2_signal("192.168.0.117", timeout=0.25) is False


def test_discover_go2_candidates_maps_lan_discovery_devices(monkeypatch) -> None:
    monkeypatch.setattr(
        connectivity,
        "discover",
        lambda timeout: [
            Go2Device(serial="SN123", ip="192.168.0.117", iface="en0", mac="AA:BB"),
        ],
    )

    candidates = tuple(connectivity.discover_go2_candidates(timeout=0.5))

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.address == "192.168.0.117"
    assert candidate.identity == "SN123"
    assert candidate.interface == "en0"
    assert candidate.transport == "webrtc"
    assert candidate.metadata == {"mac": "AA:BB"}
