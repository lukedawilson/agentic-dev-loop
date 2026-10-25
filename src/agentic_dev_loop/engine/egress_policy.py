"""Egress policy assembly and target matching (unit-5).

The pure half of the egress model: ``assemble_egress_policy`` expands preset
references into a resolved ``EgressPolicy`` and classifies its class;
``match_target`` decides one connection (BR-6). The proxy (egress_proxy.py) is
the enforcement point that calls these; both are Docker-free and portable.
"""

from __future__ import annotations

from dataclasses import dataclass

from agentic_dev_loop.core.config import (
    EgressSpec, parse_egress_port, parse_egress_target,
)
from .presets import expand_presets

ALLOWLIST = "allowlist"
# The explicit broad-but-logged browser class (BR-7): no target filtering,
# every connection logged host-side.
BROWSER = "browser"


@dataclass(frozen=True)
class TargetEntry:
    """One resolved allowlist target (BR-6.1): a domain-shaped host with an
    optional port constraint. ``host`` may also be a stable IP literal (the
    Q2 exception); it is never resolved to an IP at assembly."""

    host: str
    port: int | None = None

    @classmethod
    def from_spec(cls, spec: str) -> "TargetEntry":
        host, port = parse_egress_target(spec)
        return cls(host=host, port=port)

    def to_spec(self) -> str:
        if self.port is None:
            return self.host
        return f"{self.host}:{self.port}"


@dataclass(frozen=True)
class EgressPolicy:
    """The resolved egress policy: an expanded target set plus its class.
    ``browser`` (broad-but-logged) allows everything; ``allowlist`` denies
    everything not named (BR-6.2)."""

    targets: tuple[TargetEntry, ...] = ()
    policy_class: str = ALLOWLIST

    def to_spec(self) -> EgressSpec:
        cls = None if self.policy_class == ALLOWLIST else BROWSER
        return EgressSpec(
            class_=cls,
            presets=[],
            targets=[t.to_spec() for t in self.targets],
        )


def assemble_egress_policy(egress: EgressSpec | None) -> EgressPolicy:
    """Resolve the step's egress declaration into a policy (Q9/BR-7/BR-8).

    ``None`` yields a strict empty allowlist (deny all - the engine never
    adds an undeclared target, BR-8.3). ``class: browser`` yields the explicit
    broad-but-logged class with no target filtering. Anything else expands
    presets then explicit targets, deterministically, and classifies strict.
    """
    if egress is None:
        return EgressPolicy()
    cls = egress.class_ or ALLOWLIST
    if cls == BROWSER:
        return EgressPolicy(policy_class=BROWSER)
    entries: list[TargetEntry] = []
    if egress.presets:
        for spec in expand_presets(egress.presets):
            entries.append(TargetEntry.from_spec(spec))
    for spec in egress.targets:
        entries.append(TargetEntry.from_spec(spec))
    return EgressPolicy(targets=tuple(entries), policy_class=ALLOWLIST)


def _split_host_port(hostname: str) -> tuple[str, int | None]:
    """Split a CONNECT target into (host, port). Bare hostnames carry no port."""
    value = hostname.strip()
    if value.startswith("["):
        end = value.find("]")
        if end == -1:
            return value, None
        host = value[1:end]
        rest = value[end + 1:]
        if rest.startswith(":"):
            return host, parse_egress_port(hostname, rest[1:])
        return host, None
    if value.count(":") == 1:
        host, port = value.rsplit(":", 1)
        if port.isdigit():
            return host, int(port)
        return value, None
    return value, None


def match_target(hostname: str, policy: EgressPolicy) -> bool:
    """Deny-by-default hostname match against the expanded allowlist (BR-6).

    The browser class allows everything (BR-7). An allowlist entry matches on
    exact hostname; an entry carrying a port also constrains the connection
    port, taken from the ``host:port`` CONNECT-target form when present.
    """
    if policy.policy_class == BROWSER:
        return True
    host, port = _split_host_port(hostname)
    for entry in policy.targets:
        if entry.host != host:
            continue
        if entry.port is not None and entry.port != port:
            continue
        return True
    return False
