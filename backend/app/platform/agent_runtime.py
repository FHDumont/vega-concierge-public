"""Local agent-runtime registration.

This layer owns only stable runtime metadata. Individual agents declare their
Agent Control boundaries locally; adapters such as Galileo consume the resulting
registry without carrying an application-wide list of agent steps.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

StepType = Literal["llm", "tool"]
ControlPhase = Literal["pre", "post"]


@dataclass(frozen=True)
class AgentControlStep:
    """One externally registered Agent Control boundary."""

    type: StepType
    name: str
    phase: ControlPhase | None = None

    def as_dict(self) -> dict[str, str]:
        return {"type": self.type, "name": self.name}


@dataclass(frozen=True)
class AgentRegistration:
    """Runtime metadata published by one local agent package."""

    name: str
    control_steps: tuple[AgentControlStep, ...] = ()


_registrations: dict[str, AgentRegistration] = {}


def register_agent(registration: AgentRegistration) -> AgentRegistration:
    """Publish an agent registration once, rejecting conflicting definitions."""
    _validate_registration(registration)
    current = _registrations.get(registration.name)
    if current is None:
        _registrations[registration.name] = registration
        return registration
    if current != registration:
        raise ValueError(f"agent {registration.name!r} is already registered differently")
    return current


def registered_agents() -> tuple[AgentRegistration, ...]:
    """Return locally registered agents in their declaration order."""
    return tuple(_registrations.values())


def registered_control_steps() -> tuple[AgentControlStep, ...]:
    """Return unique control steps in local-agent declaration order."""
    steps: list[AgentControlStep] = []
    seen: set[tuple[str, str]] = set()
    for registration in _registrations.values():
        for step in registration.control_steps:
            key = (step.type, step.name)
            if key not in seen:
                seen.add(key)
                steps.append(step)
    return tuple(steps)


def control_features(phase: ControlPhase) -> frozenset[str]:
    """Return LLM feature names registered for a Control evaluation phase."""
    return frozenset(
        step.name
        for step in registered_control_steps()
        if step.type == "llm" and step.phase == phase
    )


def _validate_registration(registration: AgentRegistration) -> None:
    if not registration.name.strip():
        raise ValueError("agent registration name must not be blank")
    seen: set[tuple[str, str]] = set()
    for step in registration.control_steps:
        if not step.name.strip():
            raise ValueError(f"agent {registration.name!r} has a blank control step name")
        if step.type not in {"llm", "tool"}:
            raise ValueError(f"unsupported control step type: {step.type!r}")
        if step.type == "llm" and step.phase not in {"pre", "post"}:
            raise ValueError(f"LLM step {step.name!r} must declare a control phase")
        if step.type == "tool" and step.phase is not None:
            raise ValueError(f"tool step {step.name!r} must not declare a control phase")
        key = (step.type, step.name)
        if key in seen:
            raise ValueError(f"agent {registration.name!r} repeats control step {step.name!r}")
        seen.add(key)
