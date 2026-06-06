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

"""Startup preflights for Quest teleop blueprints."""

from __future__ import annotations

from dimos.core.coordination.preflight import PreflightResult
from dimos.core.global_config import GlobalConfig
from dimos.teleop.phone.startup import is_loopback_host


def quest_teleop_lan_ui_preflight(config: GlobalConfig) -> PreflightResult:
    """Warn when Quest teleop UI servers are still loopback-only."""
    if not is_loopback_host(config.listen_host):
        return PreflightResult.ok()

    return PreflightResult.ok(
        warnings=(
            "Quest teleop is loopback-only. To expose the headset UI to LAN devices, "
            "restart with `dimos --listen-host 0.0.0.0 run <blueprint>` only on a "
            "trusted local network.",
        ),
    )


__all__ = ["quest_teleop_lan_ui_preflight"]
