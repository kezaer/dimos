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

from dataclasses import replace
import json

import numpy as np
from typer.testing import CliRunner

from dimos.msgs.sensor_msgs.Image import Image
from dimos.robot.unitree.go2.cli import doctor as go2_doctor
from dimos.robot.unitree.go2.cli.go2tool import app
from dimos.robot.unitree.go2.lan_discovery import Go2Device


def _fake_report(
    *,
    robot_level: go2_doctor.CheckLevel = go2_doctor.CheckLevel.OK,
    port_level: go2_doctor.CheckLevel = go2_doctor.CheckLevel.OK,
    next_steps: list[str] | None = None,
) -> go2_doctor.Go2DoctorReport:
    robot_ip = "192.168.0.117"
    return go2_doctor.Go2DoctorReport(
        robot=go2_doctor.RobotCheck(
            level=robot_level,
            robot_ip=robot_ip,
            signal_port=go2_doctor.GO2_SIGNAL_PORT,
            signal_reachable=robot_level is go2_doctor.CheckLevel.OK,
            discovered=[],
            message="robot check",
        ),
        interfaces=[
            go2_doctor.LocalInterface("en0", "192.168.0.105", "255.255.255.0", True),
        ],
        run=go2_doctor.RunCheck(
            level=go2_doctor.CheckLevel.OK,
            run_id="run-1",
            pid=123,
            blueprint="teleop-phone-go2",
            log_dir="/tmp/dimos",
            message="running",
        ),
        ports=[
            go2_doctor.PortCheck(
                level=port_level,
                name="Command center",
                port=go2_doctor.COMMAND_CENTER_PORT,
                listening=True,
                bind_hosts=["0.0.0.0"],
                lan_reachable=port_level is go2_doctor.CheckLevel.OK,
                message="port check",
            ),
        ],
        image=go2_doctor.ImageCheck(
            level=go2_doctor.CheckLevel.SKIP,
            checked=False,
            topic=None,
            width=None,
            height=None,
            message="image skipped",
            attempted_topics=["jpeg_lcm:/color_image"],
        ),
        suggested_urls=["http://192.168.0.105:7779/command-center"],
        next_steps=next_steps or [],
    )


def test_check_robot_uses_single_discovered_ip(monkeypatch):
    monkeypatch.setattr(
        go2_doctor,
        "discover",
        lambda timeout: [Go2Device(serial="SN123", ip="192.168.0.117", iface="en0", mac="AA:BB")],
    )
    monkeypatch.setattr(go2_doctor, "_signal_endpoint_reachable", lambda host, port, timeout: True)

    check = go2_doctor.check_robot(
        None,
        discover_lan=True,
        discovery_timeout=0.01,
        connect_timeout=0.01,
    )

    assert check.level is go2_doctor.CheckLevel.OK
    assert check.robot_ip == "192.168.0.117"
    assert check.signal_reachable is True
    assert check.discovered[0]["serial"] == "SN123"


def test_check_robot_warns_when_discovered_robot_signal_port_is_unreachable(monkeypatch):
    monkeypatch.setattr(
        go2_doctor,
        "discover",
        lambda timeout: [Go2Device(serial="SN123", ip="192.168.0.117", iface="en0", mac=None)],
    )
    monkeypatch.setattr(go2_doctor, "_signal_endpoint_reachable", lambda host, port, timeout: False)

    check = go2_doctor.check_robot(
        None,
        discover_lan=True,
        discovery_timeout=0.01,
        connect_timeout=0.01,
    )

    assert check.level is go2_doctor.CheckLevel.WARN
    assert check.robot_ip == "192.168.0.117"
    assert check.signal_reachable is False


def test_check_robot_reports_discovered_mismatch(monkeypatch):
    monkeypatch.setattr(
        go2_doctor,
        "discover",
        lambda timeout: [Go2Device(serial="SN123", ip="192.168.0.117", iface="en0", mac=None)],
    )
    monkeypatch.setattr(go2_doctor, "_signal_endpoint_reachable", lambda host, port, timeout: False)

    check = go2_doctor.check_robot(
        "192.168.0.200",
        discover_lan=True,
        discovery_timeout=0.01,
        connect_timeout=0.01,
    )

    assert check.level is go2_doctor.CheckLevel.FAIL
    assert "192.168.0.117" in check.message


def test_check_ports_classifies_lan_and_localhost(monkeypatch):
    connectable_ports: list[int] = []

    def fake_connectable_hosts(port: int, hosts: list[str], timeout: float = 0.15) -> list[str]:
        connectable_ports.append(port)
        return []

    endpoints = [
        go2_doctor.UiEndpoint("Command center", 7779, "http"),
        go2_doctor.UiEndpoint("Phone teleop", 8444, "https"),
        go2_doctor.UiEndpoint("Missing", 12345, "tcp"),
    ]
    monkeypatch.setattr(
        go2_doctor,
        "_tcp_listeners",
        lambda required_ports=None: {
            7779: ["0.0.0.0"],
            8444: ["127.0.0.1"],
        },
    )
    monkeypatch.setattr(go2_doctor, "_connectable_hosts", fake_connectable_hosts)

    checks = {check.port: check for check in go2_doctor.check_ports(endpoints)}

    assert checks[7779].level is go2_doctor.CheckLevel.OK
    assert checks[7779].lan_reachable is True
    assert checks[8444].level is go2_doctor.CheckLevel.WARN
    assert checks[8444].lan_reachable is False
    assert checks[12345].listening is False
    assert checks[12345].lan_reachable is None
    assert checks[12345].message == (
        "listener not detected; bind address unavailable and local probes did not connect"
    )
    assert connectable_ports == [12345]


def test_check_ports_trusts_lan_bind_even_when_local_tcp_probe_fails(monkeypatch):
    endpoints = [go2_doctor.UiEndpoint("Command center", 7779, "http")]
    monkeypatch.setattr(
        go2_doctor,
        "_tcp_listeners",
        lambda required_ports=None: {7779: ["0.0.0.0"]},
    )
    monkeypatch.setattr(go2_doctor, "_connectable_hosts", lambda port, hosts, timeout=0.15: [])

    check = go2_doctor.check_ports(endpoints, probe_hosts=["127.0.0.1"])[0]

    assert check.level is go2_doctor.CheckLevel.OK
    assert check.listening is True
    assert check.lan_reachable is True
    assert "LAN devices should be able to connect" in check.message


def test_parse_lsof_tcp_listener_normalizes_wildcard():
    line = "Python  10190 user  28u IPv4 0x0 0t0 TCP *:7779 (LISTEN)"

    assert go2_doctor._parse_lsof_tcp_listener(line) == ("0.0.0.0", 7779)


def test_host_is_lan_reachable_classifies_loopback_unspecified_and_link_local() -> None:
    assert go2_doctor._host_is_lan_reachable("0.0.0.0") is True
    assert go2_doctor._host_is_lan_reachable("::") is True
    assert go2_doctor._host_is_lan_reachable("192.168.0.105") is True
    assert go2_doctor._host_is_lan_reachable("127.0.0.1") is False
    assert go2_doctor._host_is_lan_reachable("::1") is False
    assert go2_doctor._host_is_lan_reachable("localhost") is False
    assert go2_doctor._host_is_lan_reachable("fe80::1%en0") is False


def test_tcp_listeners_merges_lsof_when_required_ports_are_missing(monkeypatch):
    class FakeAddress:
        ip = "127.0.0.1"
        port = 7779

    class FakeConnection:
        status = go2_doctor.psutil.CONN_LISTEN
        laddr = FakeAddress()

    monkeypatch.setattr(go2_doctor.psutil, "net_connections", lambda kind: [FakeConnection()])
    monkeypatch.setattr(
        go2_doctor,
        "_lsof_tcp_listeners",
        lambda: {
            7779: ["0.0.0.0"],
            8444: ["0.0.0.0"],
        },
    )

    listeners = go2_doctor._tcp_listeners(required_ports=[7779, 8444])

    assert listeners == {
        7779: ["0.0.0.0", "127.0.0.1"],
        8444: ["0.0.0.0"],
    }


def test_tcp_listeners_skips_lsof_when_required_ports_are_present(monkeypatch):
    class FakeAddress:
        ip = "0.0.0.0"
        port = 7779

    class FakeConnection:
        status = go2_doctor.psutil.CONN_LISTEN
        laddr = FakeAddress()

    monkeypatch.setattr(go2_doctor.psutil, "net_connections", lambda kind: [FakeConnection()])
    monkeypatch.setattr(
        go2_doctor,
        "_lsof_tcp_listeners",
        lambda: (_ for _ in ()).throw(AssertionError("lsof should not be called")),
    )

    assert go2_doctor._tcp_listeners(required_ports=[7779]) == {7779: ["0.0.0.0"]}


def test_check_image_receives_frame_and_cleans_up(monkeypatch):
    unsubscribed: list[str] = []
    stopped: list[str] = []

    class FakeTransport:
        def __init__(self, topic: str) -> None:
            self.topic = topic

        def stop(self) -> None:
            stopped.append(self.topic)

    def fake_subscribe_pubsub_uri(topic, callback, *, msg_type=None):  # type: ignore[no-untyped-def]
        callback(Image(data=np.zeros((7, 11, 3), dtype=np.uint8)))
        return FakeTransport(topic), lambda: unsubscribed.append(topic)

    monkeypatch.setattr(
        "dimos.protocol.pubsub.registry.subscribe_pubsub_uri",
        fake_subscribe_pubsub_uri,
    )

    check = go2_doctor.check_image(
        enabled=True,
        topics=["jpeg_lcm:/color_image"],
        timeout=0.01,
    )

    assert check.level is go2_doctor.CheckLevel.OK
    assert check.width == 11
    assert check.height == 7
    assert unsubscribed == ["jpeg_lcm:/color_image"]
    assert stopped == ["jpeg_lcm:/color_image"]


def test_signal_probe_failure_is_warning_when_runtime_image_is_live():
    robot = go2_doctor.RobotCheck(
        level=go2_doctor.CheckLevel.FAIL,
        robot_ip="192.168.0.117",
        signal_port=go2_doctor.GO2_SIGNAL_PORT,
        signal_reachable=False,
        discovered=[],
        message="signal probe failed.",
    )
    image = go2_doctor.ImageCheck(
        level=go2_doctor.CheckLevel.OK,
        checked=True,
        topic="pshm:color_image",
        width=1280,
        height=720,
        message="received frame",
        attempted_topics=["pshm:color_image"],
    )

    check = go2_doctor._adjust_robot_check_with_runtime_evidence(robot, image)

    assert check.level is go2_doctor.CheckLevel.WARN
    assert "WebRTC data plane is active" in check.message


def test_suggested_urls_prefer_robot_subnet_interfaces():
    interfaces = [
        go2_doctor.LocalInterface("tailscale0", "100.64.0.10", None, False),
        go2_doctor.LocalInterface("en0", "192.168.0.105", "255.255.255.0", True),
    ]
    ports = [
        go2_doctor.PortCheck(
            level=go2_doctor.CheckLevel.OK,
            name="Command center",
            port=go2_doctor.COMMAND_CENTER_PORT,
            listening=True,
            bind_hosts=["0.0.0.0"],
            lan_reachable=True,
            message="listening",
        ),
        go2_doctor.PortCheck(
            level=go2_doctor.CheckLevel.OK,
            name="Quest teleop",
            port=go2_doctor.QUEST_TELEOP_PORT,
            listening=True,
            bind_hosts=["0.0.0.0"],
            lan_reachable=True,
            message="listening",
        ),
        go2_doctor.PortCheck(
            level=go2_doctor.CheckLevel.OK,
            name="Phone teleop",
            port=go2_doctor.PHONE_TELEOP_PORT,
            listening=True,
            bind_hosts=["0.0.0.0"],
            lan_reachable=True,
            message="listening",
        ),
    ]

    urls = go2_doctor.suggested_urls(interfaces, ports)

    assert urls == [
        "http://192.168.0.105:7779/command-center",
        "https://192.168.0.105:8443/teleop",
        "https://192.168.0.105:8444/teleop",
    ]


def test_local_interfaces_skip_link_local_addresses(monkeypatch):
    import socket
    from types import SimpleNamespace

    monkeypatch.setattr(
        go2_doctor.psutil,
        "net_if_addrs",
        lambda: {
            "en0": [
                SimpleNamespace(
                    family=socket.AF_INET,
                    address="169.254.26.130",
                    netmask="255.255.0.0",
                ),
                SimpleNamespace(
                    family=socket.AF_INET,
                    address="192.168.0.105",
                    netmask="255.255.255.0",
                ),
            ],
        },
    )

    interfaces = go2_doctor.local_interfaces()

    assert [interface.ip for interface in interfaces] == ["192.168.0.105"]


def test_suggested_urls_avoid_unmatched_interfaces_when_robot_ip_is_known():
    interfaces = [
        go2_doctor.LocalInterface("tailscale0", "100.64.0.10", None, False),
    ]
    ports = [
        go2_doctor.PortCheck(
            level=go2_doctor.CheckLevel.OK,
            name="Command center",
            port=go2_doctor.COMMAND_CENTER_PORT,
            listening=True,
            bind_hosts=["0.0.0.0"],
            lan_reachable=True,
            message="listening",
        ),
    ]

    assert go2_doctor.suggested_urls(interfaces, ports, robot_ip="192.168.0.117") == []


def test_format_report_marks_unmatched_robot_interfaces_as_warning():
    report = _fake_report()
    report = replace(
        report,
        interfaces=[go2_doctor.LocalInterface("tailscale0", "100.64.0.10", None, False)],
        suggested_urls=[],
    )

    output = go2_doctor.format_report(report)

    assert "[WARN] tailscale0: 100.64.0.10 (not in robot subnet)" in output


def test_format_report_includes_next_steps():
    output = go2_doctor.format_report(_fake_report(next_steps=["Run `example`."]))

    assert "Next steps" in output
    assert "1. Run `example`." in output


def test_suggested_next_steps_put_global_listen_host_before_run() -> None:
    report = _fake_report(port_level=go2_doctor.CheckLevel.WARN)

    steps = go2_doctor.suggested_next_steps(report)

    assert any("dimos --listen-host 0.0.0.0 run teleop-phone-go2" in step for step in steps)
    assert all("run teleop-phone-go2 --listen-host" not in step for step in steps)


def test_suggested_next_steps_do_not_check_image_before_stack_is_running() -> None:
    report = replace(
        _fake_report(robot_level=go2_doctor.CheckLevel.FAIL),
        run=go2_doctor.RunCheck(
            level=go2_doctor.CheckLevel.WARN,
            run_id=None,
            pid=None,
            blueprint=None,
            log_dir=None,
            message="No running DimOS instance found.",
        ),
    )

    steps = go2_doctor.suggested_next_steps(report)

    assert any("dimos go2tool doctor --robot-ip 192.168.0.117" in step for step in steps)
    assert all("--check-image" not in step for step in steps)


def test_default_ui_endpoints_scope_to_running_phone_blueprint() -> None:
    endpoints = go2_doctor.default_ui_endpoints(4040, blueprint="teleop-phone-go2")

    assert [endpoint.port for endpoint in endpoints] == [
        go2_doctor.COMMAND_CENTER_PORT,
        go2_doctor.PHONE_TELEOP_PORT,
        go2_doctor.RERUN_WEB_VIEWER_PORT,
        go2_doctor.RERUN_GRPC_PORT,
        4040,
    ]


def test_default_ui_endpoints_scope_to_running_quest_blueprint() -> None:
    endpoints = go2_doctor.default_ui_endpoints(4040, blueprint="teleop-quest-go2")

    assert [endpoint.port for endpoint in endpoints] == [go2_doctor.QUEST_TELEOP_PORT]


def test_default_ui_endpoints_scope_keyboard_only_go2_blueprint_to_no_ui() -> None:
    endpoints = go2_doctor.default_ui_endpoints(
        4040,
        blueprint="unitree-go2-webrtc-keyboard-teleop",
    )

    assert endpoints == []


def test_default_ui_endpoints_scope_direct_go2_module_to_no_ui() -> None:
    endpoints = go2_doctor.default_ui_endpoints(4040, blueprint="go2-connection")

    assert endpoints == []


def test_default_ui_endpoints_keep_full_set_without_blueprint_context() -> None:
    endpoints = go2_doctor.default_ui_endpoints(4040)

    assert [endpoint.port for endpoint in endpoints] == [
        go2_doctor.COMMAND_CENTER_PORT,
        go2_doctor.QUEST_TELEOP_PORT,
        go2_doctor.PHONE_TELEOP_PORT,
        go2_doctor.RERUN_WEB_VIEWER_PORT,
        go2_doctor.RERUN_GRPC_PORT,
        4040,
    ]


def test_collect_report_scopes_default_endpoints_to_running_blueprint(monkeypatch) -> None:
    captured_ports: list[int] = []
    monkeypatch.setattr(
        go2_doctor,
        "check_robot",
        lambda *args, **kwargs: go2_doctor.RobotCheck(
            level=go2_doctor.CheckLevel.OK,
            robot_ip="192.168.0.117",
            signal_port=go2_doctor.GO2_SIGNAL_PORT,
            signal_reachable=True,
            discovered=[],
            message="robot ok",
        ),
    )
    monkeypatch.setattr(go2_doctor, "local_interfaces", lambda robot_ip=None: [])
    monkeypatch.setattr(
        go2_doctor,
        "check_run",
        lambda: go2_doctor.RunCheck(
            level=go2_doctor.CheckLevel.OK,
            run_id="run-1",
            pid=123,
            blueprint="teleop-quest-go2",
            log_dir="/tmp/dimos",
            message="running",
        ),
    )

    def fake_check_ports(endpoints, *, probe_hosts=None):  # type: ignore[no-untyped-def]
        captured_ports.extend(endpoint.port for endpoint in endpoints)
        return []

    monkeypatch.setattr(go2_doctor, "check_ports", fake_check_ports)
    monkeypatch.setattr(
        go2_doctor,
        "check_image",
        lambda **kwargs: go2_doctor.ImageCheck(
            level=go2_doctor.CheckLevel.SKIP,
            checked=False,
            topic=None,
            width=None,
            height=None,
            message="image skipped",
            attempted_topics=[],
        ),
    )

    go2_doctor.collect_report(
        robot_ip="192.168.0.117",
        discover_lan=False,
        discovery_timeout=0.01,
        connect_timeout=0.01,
        signal_port=go2_doctor.GO2_SIGNAL_PORT,
        endpoints=None,
        rerun_websocket_port=4040,
        check_image_enabled=False,
    )

    assert captured_ports == [go2_doctor.QUEST_TELEOP_PORT]


def test_go2tool_doctor_command_is_registered():
    result = CliRunner().invoke(app, ["doctor", "--help"])

    assert result.exit_code == 0
    assert "without sending motion commands" in result.output


def test_go2tool_doctor_json_uses_root_robot_ip_and_custom_ports(monkeypatch):
    captured: dict[str, object] = {}

    def fake_collect_report(**kwargs):
        captured.update(kwargs)
        return _fake_report()

    monkeypatch.setattr(go2_doctor, "collect_report", fake_collect_report)

    result = CliRunner().invoke(
        app,
        [
            "doctor",
            "--json",
            "--no-discover",
            "--ui-port",
            "1234",
            "--ui-port",
            "5678",
        ],
        obj={"robot_ip": "192.168.0.117", "rerun_websocket_server_port": 4040},
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["robot"]["robot_ip"] == "192.168.0.117"
    assert captured["robot_ip"] == "192.168.0.117"
    assert captured["discover_lan"] is False
    assert captured["rerun_websocket_port"] == 4040
    endpoints = captured["endpoints"]
    assert [endpoint.port for endpoint in endpoints] == [1234, 5678]


def test_go2tool_doctor_defers_default_endpoint_selection_to_report(monkeypatch):
    captured: dict[str, object] = {}

    def fake_collect_report(**kwargs):
        captured.update(kwargs)
        return _fake_report()

    monkeypatch.setattr(go2_doctor, "collect_report", fake_collect_report)

    result = CliRunner().invoke(
        app,
        [
            "doctor",
            "--robot-ip",
            "192.168.0.117",
            "--discovery-timeout",
            "0.25",
            "--connect-timeout",
            "0.35",
            "--signal-port",
            "1234",
            "--check-image",
            "--image-topic",
            "jpeg_lcm:/custom_color",
            "--image-topic",
            "pshm:custom_color",
            "--image-timeout",
            "0.45",
        ],
        obj={"robot_ip": "192.168.0.200", "rerun_websocket_server_port": 4040},
    )

    assert result.exit_code == 0
    assert captured["robot_ip"] == "192.168.0.117"
    assert captured["discovery_timeout"] == 0.25
    assert captured["connect_timeout"] == 0.35
    assert captured["signal_port"] == 1234
    assert captured["rerun_websocket_port"] == 4040
    assert captured["check_image_enabled"] is True
    assert captured["image_topics"] == ["jpeg_lcm:/custom_color", "pshm:custom_color"]
    assert captured["image_timeout"] == 0.45
    assert captured["endpoints"] is None


def test_go2tool_doctor_strict_exits_nonzero_on_warning(monkeypatch):
    monkeypatch.setattr(
        go2_doctor,
        "collect_report",
        lambda **kwargs: _fake_report(port_level=go2_doctor.CheckLevel.WARN),
    )

    result = CliRunner().invoke(app, ["doctor", "--strict"])

    assert result.exit_code == 1
    assert "Go2 local Wi-Fi teleop doctor" in result.output
