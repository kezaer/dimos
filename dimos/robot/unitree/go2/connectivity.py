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

"""Go2 connectivity primitives shared by startup preflights and diagnostics."""

from __future__ import annotations

from collections.abc import Sequence

import requests

from dimos.hardware.preflight import HardwareCandidate
from dimos.robot.unitree.go2.lan_discovery import discover

GO2_SIGNAL_PORT = 9991


def discover_go2_candidates(timeout: float) -> Sequence[HardwareCandidate]:
    return tuple(
        HardwareCandidate(
            address=device.ip,
            identity=device.serial,
            interface=device.iface,
            transport="webrtc",
            metadata={"mac": device.mac},
        )
        for device in discover(timeout=timeout)
    )


def probe_go2_signal(address: str, timeout: float, port: int = GO2_SIGNAL_PORT) -> bool:
    try:
        response = requests.get(f"http://{address}:{port}/con_notify", timeout=timeout)
    except requests.RequestException:
        return False
    return response.ok


__all__ = ["GO2_SIGNAL_PORT", "discover_go2_candidates", "probe_go2_signal"]
