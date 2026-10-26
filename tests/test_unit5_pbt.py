"""Unit-5 PBT (code-gen step 12, PBT-03): serialisation round-trips, profile
key stability, strict-default, preset-expansion determinism, export-path
invariants."""

from hypothesis import given, strategies as st

from agentic_dev_loop.core.config import EgressSpec, MountSpec
from agentic_dev_loop.engine.egress_policy import (
    TargetEntry, assemble_egress_policy,
)
from agentic_dev_loop.engine.isolation import container_profile_key
from agentic_dev_loop.engine.mounts import resolve_mount_set

mount_specs = st.lists(
    st.builds(
        MountSpec,
        path=st.sampled_from(["src", "tests", "docs", ".venv-sandbox",
                              "node_modules", "target"]),
        mode=st.sampled_from(["rw", "ro"]),
    ),
    max_size=5,
)


class TestMountRoundTrip:
    @given(specs=mount_specs)
    def test_round_trip(self, specs):
        for spec in specs:
            assert MountSpec(**spec.model_dump()) == spec

    @given(specs=mount_specs)
    def test_resolve_strict_default_and_idempotence(self, specs):
        ms = resolve_mount_set(specs)
        assert resolve_mount_set(list(ms.to_specs())) == ms
        if not specs:
            assert len(ms) == 0


class TestEgressRoundTrip:
    @given(hosts=st.lists(st.sampled_from(["a.com", "b.com", "127.0.0.1"]),
                          max_size=3, unique=True))
    def test_target_round_trip(self, hosts):
        entries = tuple(TargetEntry.from_spec(h) for h in hosts)
        for entry in entries:
            assert TargetEntry.from_spec(entry.to_spec()) == entry

    @given(hosts=st.lists(st.sampled_from(["a.com", "b.com"]), max_size=3))
    def test_policy_round_trip(self, hosts):
        p = assemble_egress_policy(EgressSpec(targets=hosts))
        assert assemble_egress_policy(p.to_spec()) == p


class TestProfileKeyStability:
    @given(specs=mount_specs,
           hosts=st.lists(st.sampled_from(["a.com", "b.com", "c.org"]),
                          max_size=3, unique=True))
    def test_stable(self, specs, hosts):
        ms = resolve_mount_set(specs)
        p = assemble_egress_policy(EgressSpec(targets=hosts))
        assert container_profile_key(ms, p) == container_profile_key(ms, p)

    @given(specs=mount_specs)
    def test_equal_mounts_equal_key(self, specs):
        p = assemble_egress_policy(None)
        assert container_profile_key(resolve_mount_set(specs), p) == \
            container_profile_key(resolve_mount_set(list(specs)), p)


class TestPresetDeterminism:
    def test_expansion_deterministic(self):
        a = assemble_egress_policy(EgressSpec(presets=["package-registries"]))
        b = assemble_egress_policy(EgressSpec(presets=["package-registries"]))
        assert a == b
        assert a.targets and all(isinstance(t, TargetEntry) for t in a.targets)


class TestSyncBackExcludeDeterminism:
    """028: sync-back planning with excludes stays deterministic (PBT-03)."""

    @given(changes=st.dictionaries(
        st.sampled_from(["src/a.py", "src/bin/x.dll", "src/bin/y.dll",
                         "src/obj/z.o", "docs/readme.md", "qa/.artifacts/big"]),
        st.sampled_from(["added", "modified", "deleted", "conflict"]),
        max_size=6))
    def test_plan_deterministic_with_excludes(self, changes):
        from agentic_dev_loop.engine.sync_back import plan_sync_back
        a = plan_sync_back(changes, ["."],
                           excludes=["**/bin/**", "**/obj/**"])
        b = plan_sync_back(changes, ["."],
                           excludes=["**/bin/**", "**/obj/**"])
        assert a == b
        for rel in a.to_copy + a.to_delete + a.conflicts:
            assert "bin/" not in rel and "obj/" not in rel
