"""055 Task 1: bundled profiles, loader, version compare, merge."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentic_dev_loop.core.profiles import (
    Profile,
    ProfileError,
    ProfileIsolation,
    PremiseFile,
    bundled_profile_names,
    check_engine_min,
    load_profile,
    merge_profile,
    parse_version,
)


def test_parse_version_ordering_across_ranks():
    assert parse_version("0.9.2") == parse_version("0.9.2")
    assert parse_version("1.0.0a1") < parse_version("1.0.0a2")
    assert parse_version("1.0.0a9") < parse_version("1.0.0b1")
    assert parse_version("1.0.0b9") < parse_version("1.0.0rc1")
    assert parse_version("1.0.0rc9") < parse_version("1.0.0")
    assert parse_version("0.10.0") > parse_version("0.9.2")
    assert parse_version("0.9.2a1") < parse_version("0.9.2")


@pytest.mark.parametrize("bad", ["", "1.0", "1.0.0.0", "v1.0.0", "1.0.0z1",
                                 "1.0.0rc", "1.0.0rcx"])
def test_parse_version_rejects_malformed(bad):
    with pytest.raises(ProfileError):
        parse_version(bad)


def test_check_engine_min_blocks_with_exact_message():
    with pytest.raises(ProfileError) as exc:
        check_engine_min("0.10.0", "0.9.2")
    assert str(exc.value) == (
        'profile requires engine >= 0.10.0; installed 0.9.2. '
        'Upgrade: pipx install --force "agentic-dev-loop==0.10.0"'
    )


def test_check_engine_min_allows_equal_and_newer():
    check_engine_min("0.9.2", "0.9.2")
    check_engine_min("0.9.2", "0.10.0")


def test_bundled_profile_names():
    assert bundled_profile_names() == ["dotnet-node", "python"]


def test_bundled_python_profile_loads():
    profile = load_profile("python", "0.10.0")
    assert profile.name == "python"
    assert profile.isolation.cpus == 2
    assert profile.isolation.image.endswith(
        "sha256:890105209c127446caf20ce3dc05f0c0f5527e9d679e18cc0075e6b0ee917434")
    assert profile.gitignore_additions == [".venv-sandbox/"]
    assert profile.caches == []


def test_bundled_dotnet_node_profile_loads():
    profile = load_profile("dotnet-node", "0.10.0")
    assert profile.isolation.seccomp_profile == "bundled:chromium-userns"
    assert profile.isolation.cpus == 6
    assert profile.caches == [".nuget-packages"]
    assert [p.path for p in profile.premise_files] == ["NuGet.Config"]
    assert "globalPackagesFolder" in profile.premise_files[0].content
    assert "bin/" in profile.gitignore_additions


def test_path_profile_loads(tmp_path: Path):
    path = tmp_path / "custom.yaml"
    path.write_text(
        "name: custom\n"
        "engine_min: 0.1.0\n"
        "isolation:\n"
        "  image: example/img@sha256:abc\n"
        "  cpus: 4\n"
        "caches: [cache-a]\n"
        "gitignore_additions: [out/]\n",
        encoding="utf-8",
    )
    profile = load_profile(str(path), "0.10.0")
    assert profile.name == "custom"
    assert profile.isolation.cpus == 4
    assert profile.caches == ["cache-a"]


def test_unknown_bundled_profile_lists_names():
    with pytest.raises(ProfileError) as exc:
        load_profile("nope", "0.10.0")
    message = str(exc.value)
    assert "unknown profile 'nope'" in message
    assert "dotnet-node" in message and "python" in message


def test_bundled_profile_name_must_match_filename(tmp_path: Path, monkeypatch):
    import agentic_dev_loop.core.profiles as profiles

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "python.yaml").write_text(
        "name: not-python\nengine_min: 0.1.0\n"
        "isolation: {image: img, cpus: 1}\n", encoding="utf-8")

    class FakeRes:
        def joinpath(self, name):
            return data_dir

    monkeypatch.setattr(profiles.resources, "files", lambda _: FakeRes())
    with pytest.raises(ProfileError):
        profiles.load_profile("python", "0.10.0")


def test_profile_isolation_merge_pipeline_wins_and_none_dropped():
    profile = Profile(
        name="p", engine_min="0.1.0",
        isolation=ProfileIsolation(image="img", cpus=2),
    )
    merged = merge_profile(
        {"isolation": {"cpus": 8, "memory": "2g"}, "caches": ["b"]}, profile)
    assert merged["isolation"] == {
        "image": "img", "cpus": 8, "memory": "2g"}
    assert "seccomp_profile" not in merged["isolation"]


def test_profile_merge_caches_union_profile_first_dedupe():
    profile = load_profile("dotnet-node", "0.10.0")
    merged = merge_profile(
        {"caches": ["qa/.browsers", ".nuget-packages"]}, profile)
    assert merged["caches"] == [".nuget-packages", "qa/.browsers"]


def test_profile_merge_leaves_other_keys_untouched():
    profile = load_profile("python", "0.10.0")
    data = {"name": "x", "steps": [{"name": "dev"}]}
    merged = merge_profile(data, profile)
    assert merged["name"] == "x"
    assert merged["steps"] == [{"name": "dev"}]
    assert merged["isolation"]["cpus"] == 2


def test_models_are_strict():
    with pytest.raises(Exception):
        PremiseFile(path="a", content="b", extra=1)
    with pytest.raises(Exception):
        ProfileIsolation(image="i", cpus=1, extra=1)
    with pytest.raises(Exception):
        Profile(name="p", engine_min="1.0.0",
                isolation={"image": "i", "cpus": 1}, surprise=True)
