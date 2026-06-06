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

import shutil

import pytest

from dimos.web.dimos_interface.api.server import FastAPIServer, ssl_decode_cert
from dimos.web.robot_web_interface import RobotWebInterface


def test_robot_web_interface_passes_host_to_fastapi_server() -> None:
    server = RobotWebInterface(host="0.0.0.0", port=8444)

    assert server.host == "0.0.0.0"
    assert server.port == 8444


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl is required")
def test_ensure_certs_generates_subject_alt_names_for_lan_hosts(tmp_path) -> None:
    cert_path, key_path = FastAPIServer._ensure_certs(
        tmp_path,
        {"localhost", "127.0.0.1", "::1", "192.168.0.105"},
    )

    cert = ssl_decode_cert(cert_path)
    san = set(cert["subjectAltName"])

    assert ("DNS", "localhost") in san
    assert ("IP Address", "127.0.0.1") in san
    assert ("IP Address", "192.168.0.105") in san
    assert key_path.endswith("key.pem")
