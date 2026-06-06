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

"""Structured blueprint preflight checks."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
import sys
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, TypeAlias

from dimos.core.global_config import GlobalConfig
from dimos.utils.logging_config import setup_logger

if TYPE_CHECKING:
    from dimos.core.coordination.blueprints import Blueprint

logger = setup_logger()


class PreflightError(RuntimeError):
    """Raised when blueprint preflights reject startup."""

    def __init__(self, errors: Sequence[str]) -> None:
        self.errors = tuple(errors)
        super().__init__("; ".join(self.errors))


@dataclass(frozen=True)
class PreflightResult:
    """Result returned by a blueprint preflight.

    Preflights run after CLI/config-file/global-config overrides are applied and
    before modules are deployed. They may report startup notes, warnings, fatal
    errors, and conservative config repairs such as resolving a hardware IP.
    """

    config_updates: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    notes: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    @classmethod
    def ok(
        cls,
        *,
        config_updates: Mapping[str, Any] | None = None,
        notes: Sequence[str] = (),
        warnings: Sequence[str] = (),
    ) -> PreflightResult:
        return cls(
            config_updates=MappingProxyType(dict(config_updates or {})),
            notes=tuple(notes),
            warnings=tuple(warnings),
        )

    @classmethod
    def fail(cls, *errors: str, warnings: Sequence[str] = ()) -> PreflightResult:
        return cls(errors=tuple(errors), warnings=tuple(warnings))

    def has_output(self) -> bool:
        return bool(self.config_updates or self.notes or self.warnings or self.errors)


PreflightCheck = Callable[[GlobalConfig], PreflightResult]
PreflightOwner: TypeAlias = type[Any] | str


@dataclass(frozen=True)
class OwnedPreflight:
    """Callable preflight with immutable owner-module metadata."""

    check: PreflightCheck
    owner_modules: tuple[PreflightOwner, ...]
    name: str | None = None

    @property
    def __name__(self) -> str:
        if self.name is not None:
            return self.name
        label = getattr(self.check, "__name__", type(self.check).__name__)
        if isinstance(label, str):
            return label
        return type(self.check).__name__

    def __call__(self, config: GlobalConfig) -> PreflightResult:
        return self.check(config)


def run_preflights(
    blueprint: Blueprint,
    config: GlobalConfig,
    *,
    exit_on_error: bool = True,
) -> list[PreflightResult]:
    """Run and apply all preflights for a blueprint.

    Returns the collected results for tests and programmatic callers. Fatal
    errors are printed and terminate startup before any modules are deployed.
    """
    errors: list[str] = []
    pending_updates: dict[str, Any] = {}
    results: list[PreflightResult] = []
    yellow = "\033[33m"
    red = "\033[31m"
    scratch_config = config.model_copy(deep=True)

    checks = tuple(
        check for check in blueprint.preflight_checks if _preflight_applies(check, blueprint)
    )

    for check in checks:
        before_state = scratch_config.model_dump()
        result = check(scratch_config)
        results.append(result)
        label = getattr(check, "__name__", type(check).__name__)
        after_state = scratch_config.model_dump()

        direct_mutations = sorted(
            key
            for key, value in after_state.items()
            if before_state.get(key) != value and key not in result.config_updates
        )
        if direct_mutations:
            scratch_config.update(**before_state)
            errors.append(
                f"{label}: mutated config directly without returning config_updates for "
                f"{', '.join(direct_mutations)}"
            )

        if result.config_updates:
            updates = dict(result.config_updates)
            scratch_config.update(**updates)
            pending_updates.update(updates)
        errors.extend(f"{label}: {error}" for error in result.errors)

    if errors:
        for label, result in _labeled_results(checks, results):
            for warning in result.warnings:
                _emit(f"Preflight {label}: {warning}", color=yellow)
        for error in errors:
            _emit(f"Error: Preflight {error}", color=red)
        if not exit_on_error:
            raise PreflightError(errors)
        sys.exit(1)

    if pending_updates:
        config.update(**pending_updates)

    for label, result in _labeled_results(checks, results):
        if result.config_updates:
            update_keys = ", ".join(sorted(result.config_updates))
            _emit(f"Preflight {label}: updated {update_keys}", color=yellow)
        for note in result.notes:
            _emit(f"Preflight {label}: {note}")
        for warning in result.warnings:
            _emit(f"Preflight {label}: {warning}", color=yellow)

    return results


def owned_preflight(
    check: PreflightCheck,
    *owner_modules: PreflightOwner,
    name: str | None = None,
) -> PreflightCheck:
    """Limit a preflight to blueprints where at least one owner module is active."""
    return OwnedPreflight(check=check, owner_modules=tuple(owner_modules), name=name)


def _labeled_results(
    checks: Sequence[PreflightCheck],
    results: list[PreflightResult],
) -> tuple[tuple[str, PreflightResult], ...]:
    return tuple(
        (getattr(check, "__name__", type(check).__name__), result)
        for check, result in zip(checks, results, strict=True)
    )


def _preflight_applies(check: PreflightCheck, blueprint: Blueprint) -> bool:
    owners = (
        check.owner_modules
        if isinstance(check, OwnedPreflight)
        else getattr(check, "__dimos_preflight_owner_modules__", ())
    )
    if not owners:
        return True
    active_owner_keys = {_owner_key(bp.module) for bp in blueprint.active_blueprints}
    return any(_owner_key(owner) in active_owner_keys for owner in owners)


def _owner_key(owner: PreflightOwner) -> str:
    if isinstance(owner, str):
        return owner
    return f"{owner.__module__}.{owner.__qualname__}"


def _emit(message: str, *, color: str = "") -> None:
    logger.info(message)
    if color:
        reset = "\033[0m"
        print(f"{color}{message}{reset}", file=sys.stderr)
    else:
        print(message, file=sys.stderr)


__all__ = [
    "OwnedPreflight",
    "PreflightCheck",
    "PreflightError",
    "PreflightResult",
    "owned_preflight",
    "run_preflights",
]
