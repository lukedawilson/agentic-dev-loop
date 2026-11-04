"""Hypothesis properties for the pure sandbox translation logic (PBT-03).

Every property targets a pure function - no I/O, no environment access, no
Docker daemon. The generated payloads must satisfy the kernel-isolation
invariants: network off, single-mount, read-only rootfs, caps dropped.
"""
from __future__ import annotations

from hypothesis import assume, given, strategies as st

from agentic_dev_loop.engine.isolation import (
    EffectiveProfile, ProbeResult,
    docker_create_payload, is_git_mutation,
    resolve_effective_profile, resolve_sandbox_env,
    select_backend, translate_mount_path,
)

profiles = st.builds(
    EffectiveProfile,
    network_off=st.booleans(),
    read_only_rootfs=st.booleans(),
    writes_only_on_mounts=st.booleans(),
    drop_all_capabilities=st.booleans(),
    git_mutations_blocked=st.booleans(),
    image=st.text(min_size=1),
    memory=st.sampled_from(["4g", "8g", "16g"]),
    cpus=st.floats(min_value=0.5, max_value=16.0),
)


class TestCreatePayloadInvariants:
    @given(profile=profiles, workspace=st.text(min_size=1), run_id=st.text(min_size=1))
    def test_network_none_always_present(self, profile, workspace, run_id):
        p = docker_create_payload(run_id=run_id, image="img", profile=profile,
                                  workspace=workspace)
        assert "--network" in p and "none" in p

    @given(profile=profiles, workspace=st.text(min_size=1), run_id=st.text(min_size=1))
    def test_single_mount_exactly_workspace(self, profile, workspace, run_id):
        p = docker_create_payload(run_id=run_id, image="img", profile=profile,
                                  workspace=workspace)
        mounts = [p[i + 1] for i, a in enumerate(p) if a == "-v"]
        assert mounts == [f"{workspace}:{workspace}"]

    @given(profile=profiles, workspace=st.text(min_size=1), run_id=st.text(min_size=1))
    def test_read_only_rootfs_and_cap_drop(self, profile, workspace, run_id):
        p = docker_create_payload(run_id=run_id, image="img", profile=profile,
                                  workspace=workspace)
        assert "--read-only" in p
        assert "--cap-drop" in p and "ALL" in p


class TestResolveSandboxEnvScrub:
    @given(declared=st.dictionaries(st.text(min_size=1),
                                    st.one_of(st.none(), st.text())),
           host=st.dictionaries(st.text(min_size=1), st.text()))
    def test_output_within_base_plus_declared(self, declared, host):
        env = resolve_sandbox_env(declared, host_env=host)
        base = {"PATH", "HOME", "LANG", "LC_ALL", "TERM"}
        # a declared null only inherits when the host has it, so the output is
        # a subset of base + declared (never a superset)
        assert set(env) <= base | set(declared)
        assert base <= set(env)

    @given(host=st.dictionaries(st.text(min_size=1), st.text()))
    def test_no_host_leak_without_declaration(self, host):
        env = resolve_sandbox_env(None, host_env=host)
        assert set(env) == {"PATH", "HOME", "LANG", "LC_ALL", "TERM"}


class TestMountTranslation:
    @given(path=st.text(min_size=1))
    def test_idempotent(self, path):
        assume("~" not in path)  # `~user` is not a mount path (home lookup)
        once = translate_mount_path(path)
        assert translate_mount_path(once) == once


class TestGitClassifierDenyByDefault:
    @given(subcommand=st.text(min_size=1))
    def test_unknown_subcommand_is_mutation(self, subcommand):
        known_read = {"status", "log", "diff", "show", "ls-files", "rev-parse",
                      "blame", "describe", "for-each-ref", "branch", "tag",
                      "remote", "--help", "help"}
        assume(not subcommand.startswith("-"))  # global flags, not subcommands
        if subcommand in known_read:
            return
        assert is_git_mutation(["git", subcommand])

    @given(subcommand=st.sampled_from(["status", "log", "diff", "show", "ls-files",
                                       "rev-parse", "blame", "describe",
                                       "for-each-ref"]))
    def test_known_read_is_allowed(self, subcommand):
        assert not is_git_mutation(["git", subcommand])

    @given(subcommand=st.text(min_size=1))
    def test_non_git_never_classified(self, subcommand):
        assert not is_git_mutation([subcommand])


class TestSelectBackendTotality:
    @given(platform=st.sampled_from(["darwin", "linux", "win32"]),
           api=st.text(min_size=1), ostype=st.sampled_from(["linux", "windows"]))
    def test_always_docker(self, platform, api, ostype):
        from agentic_dev_loop.engine.isolation import DockerBackend
        assert select_backend(platform, ProbeResult(api, ostype)) is DockerBackend
