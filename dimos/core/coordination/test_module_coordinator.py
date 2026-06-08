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

import time
from types import MappingProxyType
from typing import Any, Protocol
import uuid

import pytest

from dimos.core._test_future_annotations_helper import (
    FutureModuleIn,
    FutureModuleOut,
)
from dimos.core.coordination.blueprints import (
    DisabledModuleProxy,
    autoconnect,
)
from dimos.core.coordination.coordinator_rpc import CoordinatorRPC
from dimos.core.coordination.module_coordinator import (
    ConfiguratorError,
    ModuleCoordinator,
    RequirementError,
    _all_name_types,
    _check_requirements,
    _verify_no_conflicts_with_existing,
    _verify_no_name_conflicts,
)
from dimos.core.coordination.preflight import (
    PreflightError,
    PreflightResult,
    owned_preflight,
    run_preflights,
)
from dimos.core.coordination.worker_manager_python import WorkerManagerPython
from dimos.core.core import rpc
from dimos.core.global_config import GlobalConfig, global_config
from dimos.core.module import Module
from dimos.core.stream import In, Out
from dimos.msgs.sensor_msgs.Image import Image
from dimos.protocol.rpc.pubsubrpc import LCMRPC
from dimos.spec.utils import Spec
from dimos.utils.safe_thread_map import ExceptionGroup as ThreadMapExceptionGroup

# Disable Rerun for tests (prevents viewer spawn and gRPC flush errors)
_BUILD_WITHOUT_RERUN = MappingProxyType(
    {
        "g": {"viewer": "none"},
    }
)


class Data1:
    pass


class Data2:
    pass


class Data3:
    pass


class ModuleA(Module):
    data1: Out[Data1]
    data2: Out[Data2]

    @rpc
    def get_name(self) -> str:
        return "A, Module A"


class ModuleB(Module):
    data1: In[Data1]
    data2: In[Data2]
    data3: Out[Data3]

    module_a: ModuleA

    @rpc
    def what_is_as_name(self) -> str:
        return self.module_a.get_name()


class ModuleC(Module):
    data3: In[Data3]


class SourceModule(Module):
    color_image: Out[Data1]


class TargetModule(Module):
    remapped_data: In[Data1]


class ExistingSharedModule(Module):
    shared: Out[Data1]


class ConflictingSharedModule(Module):
    shared: Out[Data2]


class DeployFailModule(Module):
    failed_output: Out[Data1]

    def __init__(self, **kwargs: Any) -> None:
        raise RuntimeError("deploy failed")


class SlowDeployFailModule(Module):
    failed_output: Out[Data1]

    def __init__(self, **kwargs: Any) -> None:
        time.sleep(0.2)
        raise RuntimeError("deploy failed")


class BuildFailModule(Module):
    failed_output: Out[Data1]

    @rpc
    def build(self) -> None:
        raise RuntimeError("build failed")


# ModuleRef / RPC tests
class CalculatorSpec(Spec, Protocol):
    @rpc
    def compute1(self, a: int, b: int) -> int: ...

    @rpc
    def compute2(self, a: float, b: float) -> float: ...


class Calculator1(Module):
    @rpc
    def compute1(self, a: int, b: int) -> int:
        return a + b

    @rpc
    def compute2(self, a: float, b: float) -> float:
        return a + b

    @rpc
    def start(self) -> None: ...

    @rpc
    def stop(self) -> None: ...


class Calculator2(Module):
    @rpc
    def compute1(self, a: int, b: int) -> int:
        return a * b

    @rpc
    def compute2(self, a: float, b: float) -> float:
        return a * b

    @rpc
    def start(self) -> None: ...

    @rpc
    def stop(self) -> None: ...


# link to a specific module
class Mod1(Module):
    stream1: In[Image]
    calc: Calculator1

    @rpc
    def start(self) -> None:
        _ = self.calc.compute1

    @rpc
    def stop(self) -> None: ...


# link to any module that implements a spec (Autoconnect will handle it)
class Mod2(Module):
    stream1: In[Image]
    calc: CalculatorSpec

    @rpc
    def start(self) -> None:
        _ = self.calc.compute1

    @rpc
    def stop(self) -> None: ...


def test_build_happy_path() -> None:
    blueprint_set = autoconnect(ModuleA.blueprint(), ModuleB.blueprint(), ModuleC.blueprint())

    coordinator = ModuleCoordinator.build(blueprint_set, _BUILD_WITHOUT_RERUN.copy())

    try:
        assert isinstance(coordinator, ModuleCoordinator)

        module_a_instance = coordinator.get_instance(ModuleA)
        module_b_instance = coordinator.get_instance(ModuleB)
        module_c_instance = coordinator.get_instance(ModuleC)

        assert module_a_instance is not None
        assert module_b_instance is not None
        assert module_c_instance is not None

        assert module_a_instance.data1.transport is not None
        assert module_a_instance.data2.transport is not None
        assert module_b_instance.data1.transport is not None
        assert module_b_instance.data2.transport is not None
        assert module_b_instance.data3.transport is not None
        assert module_c_instance.data3.transport is not None

        assert module_a_instance.data1.transport.topic == module_b_instance.data1.transport.topic
        assert module_a_instance.data2.transport.topic == module_b_instance.data2.transport.topic
        assert module_b_instance.data3.transport.topic == module_c_instance.data3.transport.topic

        assert module_b_instance.what_is_as_name() == "A, Module A"

    finally:
        coordinator.stop()


def test_name_conflicts_are_reported() -> None:
    class ModuleA(Module):
        shared_data: Out[Data1]

    class ModuleB(Module):
        shared_data: In[Data2]

    blueprint_set = autoconnect(ModuleA.blueprint(), ModuleB.blueprint())

    try:
        _verify_no_name_conflicts(blueprint_set)
        pytest.fail("Expected ValueError to be raised")
    except ValueError as e:
        error_message = str(e)
        assert "Blueprint cannot start because there are conflicting streams" in error_message
        assert "'shared_data' has conflicting types" in error_message
        assert "Data1 in ModuleA" in error_message
        assert "Data2 in ModuleB" in error_message


def test_multiple_name_conflicts_are_reported() -> None:
    class Module1(Module):
        sensor_data: Out[Data1]
        control_signal: Out[Data2]

    class Module2(Module):
        sensor_data: In[Data2]
        control_signal: In[Data3]

    blueprint_set = autoconnect(Module1.blueprint(), Module2.blueprint())

    try:
        _verify_no_name_conflicts(blueprint_set)
        pytest.fail("Expected ValueError to be raised")
    except ValueError as e:
        error_message = str(e)
        assert "Blueprint cannot start because there are conflicting streams" in error_message
        assert "'sensor_data' has conflicting types" in error_message
        assert "'control_signal' has conflicting types" in error_message


def test_that_remapping_can_resolve_conflicts() -> None:
    class Module1(Module):
        data: Out[Data1]

    class Module2(Module):
        data: Out[Data2]  # Would conflict with Module1.data

    class Module3(Module):
        data1: In[Data1]
        data2: In[Data2]

    # Without remapping, should raise conflict error
    blueprint_set = autoconnect(Module1.blueprint(), Module2.blueprint(), Module3.blueprint())

    try:
        _verify_no_name_conflicts(blueprint_set)
        pytest.fail("Expected ValueError due to conflict")
    except ValueError as e:
        assert "'data' has conflicting types" in str(e)

    # With remapping to resolve the conflict
    blueprint_set_remapped = autoconnect(
        Module1.blueprint(), Module2.blueprint(), Module3.blueprint()
    ).remappings(
        [
            (Module1, "data", "data1"),
            (Module2, "data", "data2"),
        ]
    )

    # Should not raise any exception after remapping
    _verify_no_name_conflicts(blueprint_set_remapped)


def test_remapping() -> None:
    """Test that remapping streams works correctly."""

    # Create blueprint with remapping
    blueprint_set = autoconnect(
        SourceModule.blueprint(),
        TargetModule.blueprint(),
    ).remappings(
        [
            (SourceModule, "color_image", "remapped_data"),
        ]
    )

    # Verify remappings are stored correctly
    assert (SourceModule, "color_image") in blueprint_set.remapping_map
    assert blueprint_set.remapping_map[(SourceModule, "color_image")] == "remapped_data"

    # Verify that remapped names are used in name resolution
    all_names = _all_name_types(blueprint_set)
    assert ("remapped_data", Data1) in all_names
    # The original name shouldn't be in the name types since it's remapped
    assert ("color_image", Data1) not in all_names

    # Build and verify streams work
    coordinator = ModuleCoordinator.build(blueprint_set, _BUILD_WITHOUT_RERUN.copy())

    try:
        source_instance = coordinator.get_instance(SourceModule)
        target_instance = coordinator.get_instance(TargetModule)

        assert source_instance is not None
        assert target_instance is not None

        # Both should have transports set
        assert source_instance.color_image.transport is not None
        assert target_instance.remapped_data.transport is not None

        # They should be using the same transport (connected)
        assert (
            source_instance.color_image.transport.topic
            == target_instance.remapped_data.transport.topic
        )

        # The topic should be /remapped_data since that's the remapped name
        assert target_instance.remapped_data.transport.topic == "/remapped_data"

    finally:
        coordinator.stop()


def test_future_annotations_autoconnect() -> None:
    """Test that autoconnect works with modules using `from __future__ import annotations`."""

    blueprint_set = autoconnect(FutureModuleOut.blueprint(), FutureModuleIn.blueprint())

    coordinator = ModuleCoordinator.build(blueprint_set, _BUILD_WITHOUT_RERUN.copy())

    try:
        out_instance = coordinator.get_instance(FutureModuleOut)
        in_instance = coordinator.get_instance(FutureModuleIn)

        assert out_instance is not None
        assert in_instance is not None

        # Both should have transports set
        assert out_instance.data.transport is not None
        assert in_instance.data.transport is not None

        # They should be connected via the same transport
        assert out_instance.data.transport.topic == in_instance.data.transport.topic

    finally:
        coordinator.stop()


def test_module_ref_direct() -> None:
    coordinator = ModuleCoordinator.build(
        autoconnect(
            Calculator1.blueprint(),
            Mod1.blueprint(),
        ),
        _BUILD_WITHOUT_RERUN.copy(),
    )

    try:
        mod1 = coordinator.get_instance(Mod1)
        assert mod1 is not None
        assert mod1.calc.compute1(2, 3) == 5
        assert mod1.calc.compute2(1.5, 2.5) == 4.0
    finally:
        coordinator.stop()


def test_module_ref_spec() -> None:
    coordinator = ModuleCoordinator.build(
        autoconnect(
            Calculator1.blueprint(),
            Mod2.blueprint(),
        ),
        _BUILD_WITHOUT_RERUN.copy(),
    )

    try:
        mod2 = coordinator.get_instance(Mod2)
        assert mod2 is not None
        assert mod2.calc.compute1(4, 5) == 9
        assert mod2.calc.compute2(3.0, 0.5) == 3.5
    finally:
        coordinator.stop()


def test_disabled_modules_are_skipped_during_build() -> None:
    blueprint_set = autoconnect(
        ModuleA.blueprint(), ModuleB.blueprint(), ModuleC.blueprint()
    ).disabled_modules(ModuleC)

    coordinator = ModuleCoordinator.build(blueprint_set, _BUILD_WITHOUT_RERUN.copy())

    try:
        assert coordinator.get_instance(ModuleA) is not None
        assert coordinator.get_instance(ModuleB) is not None

        assert coordinator.get_instance(ModuleC) is None
    finally:
        coordinator.stop()


def test_disabled_module_ref_gets_noop_proxy() -> None:
    blueprint_set = autoconnect(
        Calculator1.blueprint(),
        Mod2.blueprint(),
    ).disabled_modules(Calculator1)

    coordinator = ModuleCoordinator.build(blueprint_set, _BUILD_WITHOUT_RERUN.copy())

    try:
        mod2 = coordinator.get_instance(Mod2)
        assert mod2 is not None
        # The proxy should be a _DisabledModuleProxy, not a real Calculator.
        assert isinstance(mod2.calc, DisabledModuleProxy)
        # Calling methods on it should return None (no-op).
        assert mod2.calc.compute1(1, 2) is None
    finally:
        coordinator.stop()


def test_module_ref_remap_ambiguous() -> None:
    coordinator = ModuleCoordinator.build(
        autoconnect(
            Calculator1.blueprint(),
            Calculator2.blueprint(),
            Mod2.blueprint(),
        ).remappings(
            [
                (Mod2, "calc", Calculator1),
            ]
        ),
        _BUILD_WITHOUT_RERUN.copy(),
    )

    try:
        mod2 = coordinator.get_instance(Mod2)
        assert mod2 is not None
        assert mod2.calc.compute1(2, 3) == 5
        assert mod2.calc.compute2(2.0, 3.0) == 5.0
    finally:
        coordinator.stop()


def test_load_blueprint_basic(dynamic_coordinator) -> None:
    """load_blueprint deploys, wires and starts modules the same way build() does."""
    bp = autoconnect(ModuleA.blueprint(), ModuleB.blueprint(), ModuleC.blueprint())
    dynamic_coordinator.load_blueprint(bp)

    assert dynamic_coordinator.get_instance(ModuleA) is not None
    assert dynamic_coordinator.get_instance(ModuleB) is not None
    assert dynamic_coordinator.get_instance(ModuleC) is not None

    a = dynamic_coordinator.get_instance(ModuleA)
    b = dynamic_coordinator.get_instance(ModuleB)
    c = dynamic_coordinator.get_instance(ModuleC)

    # Streams wired.
    assert a.data1.transport is not None
    assert b.data1.transport is not None
    assert a.data1.transport.topic == b.data1.transport.topic
    assert b.data3.transport.topic == c.data3.transport.topic

    # Module ref wired.
    assert b.what_is_as_name() == "A, Module A"


def test_load_blueprint_twice(dynamic_coordinator) -> None:
    """Two sequential load_blueprint calls share transports for matching streams."""
    dynamic_coordinator.load_blueprint(ModuleA.blueprint())
    dynamic_coordinator.load_blueprint(autoconnect(ModuleB.blueprint(), ModuleC.blueprint()))

    a = dynamic_coordinator.get_instance(ModuleA)
    b = dynamic_coordinator.get_instance(ModuleB)
    c = dynamic_coordinator.get_instance(ModuleC)

    assert a is not None
    assert b is not None
    assert c is not None

    # A's Out[Data1] and B's In[Data1] should share a transport.
    assert a.data1.transport.topic == b.data1.transport.topic
    assert a.data2.transport.topic == b.data2.transport.topic
    assert b.data3.transport.topic == c.data3.transport.topic


def test_load_module_convenience(dynamic_coordinator) -> None:
    """load_module is a shorthand for load_blueprint(cls.blueprint())."""
    dynamic_coordinator.load_module(ModuleA)
    assert dynamic_coordinator.get_instance(ModuleA) is not None
    assert dynamic_coordinator.get_instance(ModuleA).data1.transport is not None


def test_load_blueprint_module_ref_to_existing(dynamic_coordinator) -> None:
    """A module loaded in a second blueprint can reference one from the first."""
    dynamic_coordinator.load_blueprint(Calculator1.blueprint())
    dynamic_coordinator.load_blueprint(Mod2.blueprint())

    mod2 = dynamic_coordinator.get_instance(Mod2)
    assert mod2 is not None
    assert mod2.calc.compute1(2, 3) == 5
    assert mod2.calc.compute2(1.5, 2.5) == 4.0


def test_load_blueprint_conflict_with_existing() -> None:
    """Loading a blueprint whose stream name clashes (different type) raises ValueError."""
    from dimos.core.transport import pLCMTransport

    registry: dict[tuple[str, type], object] = {("data1", Data1): pLCMTransport("/data1")}

    class ConflictModule(Module):
        data1: In[Data2]  # same name, different type

    bp = ConflictModule.blueprint()
    with pytest.raises(ValueError, match="data1"):
        _verify_no_conflicts_with_existing(bp, registry)


def test_load_blueprint_duplicate_module_raises(dynamic_coordinator) -> None:
    """Loading a module that is already deployed raises ValueError."""
    dynamic_coordinator.load_blueprint(ModuleA.blueprint())
    with pytest.raises(ValueError, match="already deployed"):
        dynamic_coordinator.load_blueprint(ModuleA.blueprint())


class ModWithOptionalRef(Module):
    stream1: In[Image]
    calc: CalculatorSpec | None = None

    @rpc
    def start(self) -> None: ...

    @rpc
    def stop(self) -> None: ...


@pytest.fixture
def build_coordinator():
    coordinators = []

    def _build(blueprint):
        c = ModuleCoordinator.build(blueprint, _BUILD_WITHOUT_RERUN.copy())
        coordinators.append(c)
        return c

    yield _build

    for c in reversed(coordinators):
        c.stop()


@pytest.fixture
def dynamic_coordinator():
    mc = ModuleCoordinator(g=GlobalConfig(n_workers=0, viewer="none"))
    mc.start()
    yield mc
    mc.stop()


def test_optional_module_ref_with_provider(build_coordinator) -> None:
    """An optional ref resolves normally when a provider is present."""
    coordinator = build_coordinator(
        autoconnect(
            Calculator1.blueprint(),
            ModWithOptionalRef.blueprint(),
        ),
    )

    mod = coordinator.get_instance(ModWithOptionalRef)
    assert mod is not None
    assert mod.calc.compute1(2, 3) == 5


def test_optional_module_ref_without_provider(build_coordinator) -> None:
    """An optional ref is silently skipped when no provider is in the blueprint."""
    coordinator = build_coordinator(ModWithOptionalRef.blueprint())

    mod = coordinator.get_instance(ModWithOptionalRef)
    assert mod is not None


def test_load_blueprint_auto_scales_empty_pool(dynamic_coordinator) -> None:
    """A coordinator with 0 initial workers auto-adds workers on load_blueprint."""
    dynamic_coordinator.load_blueprint(ModuleA.blueprint())
    assert dynamic_coordinator.get_instance(ModuleA) is not None
    assert dynamic_coordinator.get_instance(ModuleA).data1.transport is not None


def test_check_requirements_failure(mocker) -> None:
    """A failing requirement check causes sys.exit."""
    mocker.patch("dimos.core.coordination.module_coordinator.sys.exit", side_effect=SystemExit(1))

    bp = ModuleA.blueprint().requirements(lambda: "missing GPU driver")

    with pytest.raises(SystemExit):
        _check_requirements(bp)


def test_run_preflights_applies_updates_before_requirements(capsys) -> None:
    config = GlobalConfig(robot_ip=None, viewer="none")
    seen: list[str | None] = []

    def resolve_robot_ip(gc: GlobalConfig) -> PreflightResult:
        assert gc.robot_ip is None
        return PreflightResult.ok(
            config_updates={"robot_ip": "192.168.0.117"},
            notes=("resolved test robot",),
        )

    def require_robot_ip() -> str | None:
        seen.append(config.robot_ip)
        return None if config.robot_ip == "192.168.0.117" else "robot_ip not resolved"

    bp = ModuleA.blueprint().preflights(resolve_robot_ip).requirements(require_robot_ip)

    run_preflights(bp, config)
    _check_requirements(bp)

    assert config.robot_ip == "192.168.0.117"
    assert seen == ["192.168.0.117"]
    assert "resolved test robot" in capsys.readouterr().err


def test_run_preflights_exits_on_errors(capsys) -> None:
    def fail_preflight(gc: GlobalConfig) -> PreflightResult:
        return PreflightResult.fail(f"bad robot_ip={gc.robot_ip}")

    bp = ModuleA.blueprint().preflights(fail_preflight)

    with pytest.raises(SystemExit):
        run_preflights(bp, GlobalConfig(robot_ip="192.168.0.200", viewer="none"))

    assert "bad robot_ip=192.168.0.200" in capsys.readouterr().err


def test_run_preflights_keeps_config_updates_atomic_on_errors() -> None:
    config = GlobalConfig(robot_ip=None, viewer="none")
    seen_by_second: list[str | None] = []

    def resolve_robot_ip(gc: GlobalConfig) -> PreflightResult:
        return PreflightResult.ok(config_updates={"robot_ip": "192.168.0.117"})

    def fail_after_staged_update(gc: GlobalConfig) -> PreflightResult:
        seen_by_second.append(gc.robot_ip)
        return PreflightResult.fail("late preflight failed")

    bp = ModuleA.blueprint().preflights(resolve_robot_ip, fail_after_staged_update)

    with pytest.raises(SystemExit):
        run_preflights(bp, config)

    assert seen_by_second == ["192.168.0.117"]
    assert config.robot_ip is None


def test_run_preflights_rejects_direct_config_mutation() -> None:
    config = GlobalConfig(robot_ip=None, viewer="none")

    def mutate_config_directly(gc: GlobalConfig) -> PreflightResult:
        gc.robot_ip = "192.168.0.117"
        return PreflightResult.ok()

    bp = ModuleA.blueprint().preflights(mutate_config_directly)

    with pytest.raises(SystemExit):
        run_preflights(bp, config)

    assert config.robot_ip is None


def test_run_preflights_skips_owned_checks_when_owner_module_is_disabled() -> None:
    calls: list[str] = []

    def owned_check(gc: GlobalConfig) -> PreflightResult:
        calls.append("called")
        return PreflightResult.ok(config_updates={"robot_ip": "192.168.0.117"})

    bp = (
        ModuleA.blueprint()
        .preflights(owned_preflight(owned_check, ModuleA))
        .disabled_modules(ModuleA)
    )
    config = GlobalConfig(robot_ip=None, viewer="none")

    run_preflights(bp, config)

    assert calls == []
    assert config.robot_ip is None


def test_load_blueprint_runs_preflights_before_worker_scaling(
    dynamic_coordinator,
    monkeypatch,
) -> None:
    seen_by_worker_scaling: list[str | None] = []
    python_wm = dynamic_coordinator._managers["python"]
    assert isinstance(python_wm, WorkerManagerPython)

    def resolve_robot_ip(gc: GlobalConfig) -> PreflightResult:
        return PreflightResult.ok(config_updates={"robot_ip": "192.168.0.117"})

    def record_add_workers(n_workers: int) -> None:
        seen_by_worker_scaling.append(dynamic_coordinator._global_config.robot_ip)

    monkeypatch.setattr(python_wm, "add_workers", record_add_workers)

    dynamic_coordinator.load_blueprint(
        autoconnect().global_config(n_workers=1).preflights(resolve_robot_ip)
    )

    assert seen_by_worker_scaling == ["192.168.0.117"]
    assert dynamic_coordinator._global_config.robot_ip == "192.168.0.117"


def test_load_blueprint_failing_preflight_does_not_scale_or_deploy(
    dynamic_coordinator,
    monkeypatch,
) -> None:
    add_worker_calls: list[int] = []
    original_config = dynamic_coordinator._global_config.model_dump()
    python_wm = dynamic_coordinator._managers["python"]
    assert isinstance(python_wm, WorkerManagerPython)

    def fail_preflight(gc: GlobalConfig) -> PreflightResult:
        return PreflightResult.fail("preflight rejected startup")

    def record_add_workers(n_workers: int) -> None:
        add_worker_calls.append(n_workers)

    monkeypatch.setattr(python_wm, "add_workers", record_add_workers)
    blueprint_args = {"g": {"robot_ip": "192.168.0.118"}}

    with pytest.raises(PreflightError):
        dynamic_coordinator.load_blueprint(
            ModuleA.blueprint()
            .global_config(n_workers=1, robot_ip="192.168.0.117")
            .preflights(fail_preflight),
            blueprint_args,
        )

    assert add_worker_calls == []
    assert dynamic_coordinator.get_instance(ModuleA) is None
    assert dynamic_coordinator._global_config.model_dump() == original_config
    assert blueprint_args == {"g": {"robot_ip": "192.168.0.118"}}


def test_load_blueprint_failing_requirement_does_not_scale_or_deploy(
    dynamic_coordinator,
    monkeypatch,
) -> None:
    add_worker_calls: list[int] = []
    original_config = dynamic_coordinator._global_config.model_dump()
    python_wm = dynamic_coordinator._managers["python"]
    assert isinstance(python_wm, WorkerManagerPython)

    def resolve_robot_ip(gc: GlobalConfig) -> PreflightResult:
        return PreflightResult.ok(config_updates={"robot_ip": "192.168.0.117"})

    def reject_requirement() -> str:
        return "requirement rejected startup"

    def record_add_workers(n_workers: int) -> None:
        add_worker_calls.append(n_workers)

    monkeypatch.setattr(python_wm, "add_workers", record_add_workers)

    with pytest.raises(RequirementError):
        dynamic_coordinator.load_blueprint(
            ModuleA.blueprint()
            .global_config(n_workers=1)
            .preflights(resolve_robot_ip)
            .requirements(reject_requirement)
        )

    assert add_worker_calls == []
    assert dynamic_coordinator.get_instance(ModuleA) is None
    assert dynamic_coordinator._global_config.model_dump() == original_config


def test_load_blueprint_configurator_decline_raises_without_exiting_or_mutating_config(
    dynamic_coordinator,
    mocker,
) -> None:
    original_config = dynamic_coordinator._global_config.model_dump()
    mocker.patch(
        "dimos.protocol.service.system_configurator.base.configure_system",
        side_effect=SystemExit(1),
    )

    with pytest.raises(ConfiguratorError):
        dynamic_coordinator.load_blueprint(
            ModuleA.blueprint().global_config(robot_ip="192.168.0.117")
        )

    assert dynamic_coordinator.get_instance(ModuleA) is None
    assert dynamic_coordinator._global_config.model_dump() == original_config


def test_load_blueprint_duplicate_module_does_not_scale_or_change_config(
    dynamic_coordinator,
    monkeypatch,
) -> None:
    dynamic_coordinator.load_blueprint(ModuleA.blueprint())
    add_worker_calls: list[int] = []
    original_config = dynamic_coordinator._global_config.model_dump()
    python_wm = dynamic_coordinator._managers["python"]
    assert isinstance(python_wm, WorkerManagerPython)

    def record_add_workers(n_workers: int) -> None:
        add_worker_calls.append(n_workers)

    monkeypatch.setattr(python_wm, "add_workers", record_add_workers)

    with pytest.raises(ValueError, match="already deployed"):
        dynamic_coordinator.load_blueprint(
            ModuleA.blueprint().global_config(n_workers=1, robot_ip="192.168.0.117")
        )

    assert add_worker_calls == []
    assert dynamic_coordinator._global_config.model_dump() == original_config


def test_load_blueprint_stream_conflict_does_not_scale_or_change_config(
    dynamic_coordinator,
    monkeypatch,
) -> None:
    dynamic_coordinator.load_blueprint(ExistingSharedModule.blueprint())
    add_worker_calls: list[int] = []
    original_config = dynamic_coordinator._global_config.model_dump()
    python_wm = dynamic_coordinator._managers["python"]
    assert isinstance(python_wm, WorkerManagerPython)

    def record_add_workers(n_workers: int) -> None:
        add_worker_calls.append(n_workers)

    monkeypatch.setattr(python_wm, "add_workers", record_add_workers)

    with pytest.raises(ValueError, match="existing transport"):
        dynamic_coordinator.load_blueprint(
            ConflictingSharedModule.blueprint().global_config(
                n_workers=1,
                robot_ip="192.168.0.117",
            )
        )

    assert add_worker_calls == []
    assert dynamic_coordinator.get_instance(ConflictingSharedModule) is None
    assert dynamic_coordinator._global_config.model_dump() == original_config


def test_load_blueprint_deploy_failure_keeps_existing_coordinator_alive(
    dynamic_coordinator,
) -> None:
    dynamic_coordinator.load_blueprint(ModuleA.blueprint())
    original_config = dynamic_coordinator._global_config.model_dump()
    original_transports = dict(dynamic_coordinator._transport_registry)

    with pytest.raises(ThreadMapExceptionGroup):
        dynamic_coordinator.load_blueprint(
            DeployFailModule.blueprint().global_config(robot_ip="192.168.0.117")
        )

    assert dynamic_coordinator.get_instance(ModuleA).get_name() == "A, Module A"
    assert dynamic_coordinator.get_instance(DeployFailModule) is None
    assert dynamic_coordinator._transport_registry == original_transports
    assert dynamic_coordinator._global_config.model_dump() == original_config

    dynamic_coordinator.load_blueprint(ModuleC.blueprint())
    assert dynamic_coordinator.get_instance(ModuleC) is not None


def test_load_blueprint_deploy_failure_preserves_existing_idle_workers() -> None:
    coordinator = ModuleCoordinator(g=GlobalConfig(n_workers=1, viewer="none"))
    coordinator.start()
    try:
        python_wm = coordinator._managers["python"]
        assert isinstance(python_wm, WorkerManagerPython)
        original_workers = python_wm.workers
        assert len(original_workers) == 1
        assert original_workers[0].module_count == 0

        with pytest.raises(ThreadMapExceptionGroup):
            coordinator.load_blueprint(
                autoconnect(
                    ModuleA.blueprint(),
                    SlowDeployFailModule.blueprint(),
                )
            )

        assert python_wm.workers == original_workers
        assert original_workers[0].pid is not None
        assert original_workers[0].module_count == 0
        assert coordinator.get_instance(ModuleA) is None
        assert coordinator.get_instance(SlowDeployFailModule) is None
        assert python_wm.health_check()
    finally:
        coordinator.stop()


def test_load_blueprint_build_failure_rolls_back_dynamic_load(
    dynamic_coordinator,
) -> None:
    dynamic_coordinator.load_blueprint(ModuleA.blueprint())
    original_config = dynamic_coordinator._global_config.model_dump()
    original_transports = dict(dynamic_coordinator._transport_registry)

    with pytest.raises(ThreadMapExceptionGroup):
        dynamic_coordinator.load_blueprint(
            BuildFailModule.blueprint().global_config(robot_ip="192.168.0.117")
        )

    assert dynamic_coordinator.get_instance(ModuleA).get_name() == "A, Module A"
    assert dynamic_coordinator.get_instance(BuildFailModule) is None
    assert dynamic_coordinator._transport_registry == original_transports
    assert dynamic_coordinator._global_config.model_dump() == original_config

    dynamic_coordinator.load_blueprint(ModuleC.blueprint())
    assert dynamic_coordinator.get_instance(ModuleC) is not None


def test_build_failing_preflight_does_not_mutate_global_config() -> None:
    original_config = global_config.model_dump()
    blueprint_args: dict[str, object] = {"g": {"robot_ip": "192.168.0.118"}}

    def fail_preflight(gc: GlobalConfig) -> PreflightResult:
        assert gc.robot_ip == "192.168.0.118"
        return PreflightResult.fail("preflight rejected build")

    try:
        with pytest.raises(SystemExit):
            ModuleCoordinator.build(
                ModuleA.blueprint()
                .global_config(n_workers=1, robot_ip="192.168.0.117")
                .preflights(fail_preflight),
                blueprint_args,
            )
    finally:
        global_config.update(**original_config)

    assert global_config.model_dump() == original_config
    assert blueprint_args == {"g": {"robot_ip": "192.168.0.118"}}


def test_build_failing_requirement_rolls_back_global_config() -> None:
    original_config = global_config.model_dump()
    seen_by_requirement: list[str | None] = []

    def resolve_robot_ip(gc: GlobalConfig) -> PreflightResult:
        return PreflightResult.ok(config_updates={"robot_ip": "192.168.0.117"})

    def reject_requirement() -> str:
        seen_by_requirement.append(global_config.robot_ip)
        return "requirement rejected build"

    try:
        with pytest.raises(SystemExit):
            ModuleCoordinator.build(
                ModuleA.blueprint().preflights(resolve_robot_ip).requirements(reject_requirement),
                _BUILD_WITHOUT_RERUN.copy(),
            )
    finally:
        global_config.update(**original_config)

    assert seen_by_requirement == ["192.168.0.117"]
    assert global_config.model_dump() == original_config


def test_build_name_conflict_rolls_back_global_config() -> None:
    original_config = global_config.model_dump()

    class ConflictingOut(Module):
        shared: Out[Data1]

    class ConflictingIn(Module):
        shared: In[Data2]

    try:
        with pytest.raises(ValueError, match="conflicting streams"):
            ModuleCoordinator.build(
                autoconnect(ConflictingOut.blueprint(), ConflictingIn.blueprint()).global_config(
                    robot_ip="192.168.0.117"
                ),
                _BUILD_WITHOUT_RERUN.copy(),
            )
    finally:
        global_config.update(**original_config)

    assert global_config.model_dump() == original_config


def test_build_configurator_decline_rolls_back_global_config(mocker) -> None:
    original_config = global_config.model_dump()
    start = mocker.patch.object(ModuleCoordinator, "start")
    mocker.patch(
        "dimos.protocol.service.system_configurator.base.configure_system",
        side_effect=SystemExit(1),
    )

    try:
        with pytest.raises(SystemExit):
            ModuleCoordinator.build(
                ModuleA.blueprint().global_config(robot_ip="192.168.0.117"),
                _BUILD_WITHOUT_RERUN.copy(),
            )
    finally:
        global_config.update(**original_config)

    start.assert_not_called()
    assert global_config.model_dump() == original_config


def test_build_deploy_failure_stops_partial_coordinator_and_rolls_back_config(mocker) -> None:
    original_config = global_config.model_dump()
    mocker.patch.object(ModuleCoordinator, "start")
    stop = mocker.patch.object(ModuleCoordinator, "stop")
    mocker.patch(
        "dimos.core.coordination.module_coordinator._deploy_all_modules",
        side_effect=RuntimeError("deploy failed"),
    )

    try:
        with pytest.raises(RuntimeError, match="deploy failed"):
            ModuleCoordinator.build(
                ModuleA.blueprint().global_config(robot_ip="192.168.0.117"),
                _BUILD_WITHOUT_RERUN.copy(),
            )
    finally:
        global_config.update(**original_config)

    stop.assert_called_once()
    assert global_config.model_dump() == original_config


def test_restart_module_basic(dynamic_coordinator) -> None:
    """restart_module replaces the deployed proxy with a fresh one."""
    dynamic_coordinator.load_module(ModuleA)
    old_proxy = dynamic_coordinator.get_instance(ModuleA)
    assert old_proxy is not None

    new_proxy = dynamic_coordinator.restart_module(ModuleA, reload_source=False)

    assert new_proxy is not None
    assert new_proxy is not old_proxy
    assert dynamic_coordinator.get_instance(ModuleA) is new_proxy
    assert new_proxy.get_name() == "A, Module A"


def test_restart_module_preserves_stream_wiring(dynamic_coordinator) -> None:
    """Streams stay on the same transport after restart so consumers keep receiving data."""
    dynamic_coordinator.load_blueprint(autoconnect(ModuleA.blueprint(), ModuleC.blueprint()))

    c = dynamic_coordinator.get_instance(ModuleC)
    assert c is not None
    topic_before = c.data3.transport.topic
    registry_before = dynamic_coordinator._transport_registry[("data3", Data3)]

    dynamic_coordinator.restart_module(ModuleC, reload_source=False)

    # Transport in the registry is the same parent-side object.
    assert dynamic_coordinator._transport_registry[("data3", Data3)] is registry_before

    c_after = dynamic_coordinator.get_instance(ModuleC)
    assert c_after is not None
    assert c_after is not c
    # The restarted module's stream is wired to the same topic.
    assert c_after.data3.transport.topic == topic_before


def test_restart_module_rewires_module_refs(dynamic_coordinator) -> None:
    """After restart, modules that reference the restarted class see the new proxy."""
    dynamic_coordinator.load_blueprint(autoconnect(ModuleA.blueprint(), ModuleB.blueprint()))

    b = dynamic_coordinator.get_instance(ModuleB)
    assert b is not None
    assert b.what_is_as_name() == "A, Module A"

    dynamic_coordinator.restart_module(ModuleA, reload_source=False)

    assert b.what_is_as_name() == "A, Module A"


def test_restart_consumer_rewires_outbound_refs(dynamic_coordinator) -> None:
    """Restarting a consumer re-injects its refs to existing target modules."""
    dynamic_coordinator.load_blueprint(autoconnect(ModuleA.blueprint(), ModuleB.blueprint()))

    dynamic_coordinator.restart_module(ModuleB, reload_source=False)

    b_after = dynamic_coordinator.get_instance(ModuleB)
    assert b_after is not None
    # The new ModuleB must still reach ModuleA through its outbound module_ref.
    assert b_after.what_is_as_name() == "A, Module A"


def test_restart_module_shuts_down_empty_worker(dynamic_coordinator) -> None:
    """Restart shuts down the old worker (when empty) and spawns a new one."""

    dynamic_coordinator.load_module(ModuleA)
    python_wm = dynamic_coordinator._managers["python"]
    assert isinstance(python_wm, WorkerManagerPython)

    old_worker_ids = {w.worker_id for w in python_wm.workers}
    assert len(old_worker_ids) == 1

    dynamic_coordinator.restart_module(ModuleA, reload_source=False)

    new_worker_ids = {w.worker_id for w in python_wm.workers}
    assert len(new_worker_ids) == 1
    assert new_worker_ids.isdisjoint(old_worker_ids)


def test_restart_module_calls_importlib_reload(dynamic_coordinator, mocker) -> None:
    """reload_source=True invokes importlib.reload on the module's source file."""
    dynamic_coordinator.load_module(ModuleA)

    # Stub reload so it's a no-op. Actually reloading this test module would
    # re-execute test definitions and corrupt later tests.
    mock_reload = mocker.patch(
        "dimos.core.coordination.module_coordinator.importlib.reload",
        side_effect=lambda m: m,
    )

    dynamic_coordinator.restart_module(ModuleA, reload_source=True)

    mock_reload.assert_called_once()
    reloaded_module = mock_reload.call_args.args[0]
    assert reloaded_module.__name__ == ModuleA.__module__


def _mock_reload_producing_new_class(original_class):
    """Return a reload side-effect that replaces the original class with a fresh copy."""
    new_class = type(
        original_class.__name__, original_class.__bases__, dict(original_class.__dict__)
    )
    new_class.__module__ = original_class.__module__
    new_class.__qualname__ = original_class.__qualname__

    def side_effect(mod):
        setattr(mod, original_class.__name__, new_class)
        return mod

    return side_effect, new_class


def test_get_instance_after_reload_restart(dynamic_coordinator, mocker) -> None:
    """get_instance with the original class still works after a reload restart."""
    dynamic_coordinator.load_module(ModuleA)

    side_effect, _new_class = _mock_reload_producing_new_class(ModuleA)
    mocker.patch(
        "dimos.core.coordination.module_coordinator.importlib.reload",
        side_effect=side_effect,
    )

    new_proxy = dynamic_coordinator.restart_module(ModuleA, reload_source=True)

    assert dynamic_coordinator.get_instance(ModuleA) is new_proxy


def test_double_restart_with_reload(dynamic_coordinator, mocker) -> None:
    """A second restart via the original class works after a reload restart."""
    dynamic_coordinator.load_module(ModuleA)

    side_effect1, new_class1 = _mock_reload_producing_new_class(ModuleA)
    mocker.patch(
        "dimos.core.coordination.module_coordinator.importlib.reload",
        side_effect=side_effect1,
    )
    proxy1 = dynamic_coordinator.restart_module(ModuleA, reload_source=True)

    side_effect2, _new_class2 = _mock_reload_producing_new_class(new_class1)
    mocker.patch(
        "dimos.core.coordination.module_coordinator.importlib.reload",
        side_effect=side_effect2,
    )
    proxy2 = dynamic_coordinator.restart_module(ModuleA, reload_source=True)

    assert proxy2 is not proxy1
    assert dynamic_coordinator.get_instance(ModuleA) is proxy2


def test_unload_after_reload_restart(dynamic_coordinator, mocker) -> None:
    """unload_module with the original class works after a reload restart."""
    dynamic_coordinator.load_module(ModuleA)

    side_effect, _new_class = _mock_reload_producing_new_class(ModuleA)
    mocker.patch(
        "dimos.core.coordination.module_coordinator.importlib.reload",
        side_effect=side_effect,
    )
    dynamic_coordinator.restart_module(ModuleA, reload_source=True)

    dynamic_coordinator.unload_module(ModuleA)
    assert dynamic_coordinator.get_instance(ModuleA) is None


def test_restart_preserves_remapped_streams(dynamic_coordinator) -> None:
    """Restart reconnects streams that were remapped during initial load."""
    bp = autoconnect(
        SourceModule.blueprint(),
        TargetModule.blueprint(),
    ).remappings(
        [(SourceModule, "color_image", "remapped_data")],
    )
    dynamic_coordinator.load_blueprint(bp)

    target = dynamic_coordinator.get_instance(TargetModule)
    registry_before = dynamic_coordinator._transport_registry[("remapped_data", Data1)]

    dynamic_coordinator.restart_module(SourceModule, reload_source=False)

    # The coordinator-side transport object in the registry is unchanged.
    assert dynamic_coordinator._transport_registry[("remapped_data", Data1)] is registry_before
    # The restarted proxy sees the same topic as the target.
    source_after = dynamic_coordinator.get_instance(SourceModule)
    assert source_after.color_image.transport.topic == target.remapped_data.transport.topic


def test_start_rpc_service_responds_to_ping(dynamic_coordinator, monkeypatch) -> None:
    port = 20000 + (uuid.uuid4().int % 20000)
    url = f"udpm://239.255.76.67:{port}?ttl=0"
    monkeypatch.setattr(
        "dimos.core.coordination.coordinator_rpc.LCMRPC",
        lambda **kwargs: LCMRPC(url=url, **kwargs),
    )

    dynamic_coordinator.start_rpc_service()
    client = CoordinatorRPC.connect(timeout=2.0)
    try:
        assert client.call("ping") == "pong"
    finally:
        client.stop()


def test_list_module_names(dynamic_coordinator) -> None:
    assert dynamic_coordinator.list_module_names() == []
    dynamic_coordinator.load_module(ModuleA)
    dynamic_coordinator.load_module(ModuleC)
    assert set(dynamic_coordinator.list_module_names()) == {"ModuleA", "ModuleC"}
