"""Host-side doctor checks and the default probe implementation.

Network boundaries (PyPI, image registries, service reachability) are the
only probes a test may replace; everything else runs real.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import urllib.request
from dataclasses import dataclass
from importlib import util as importlib_util
from pathlib import Path

import yaml

from agentic_dev_loop.core.profiles import Profile, ProfileError, parse_version

__all__ = ["Check", "HostProbes", "collect_checks"]

ENV_WHITELIST = frozenset({".env.example", ".env.template", ".env.sample"})
MIN_API = (1, 43)
PYPI_URL = "https://pypi.org/pypi/agentic-dev-loop/json"


@dataclass(frozen=True)
class Check:
    name: str
    severity: str
    ok: bool
    message: str = ""
    fix_text: str = ""


class HostProbes:
    """Real host-side probes. Tests replace this object, never the checks."""

    def docker_present(self) -> bool:
        return shutil.which("docker") is not None

    def daemon_info(self) -> tuple[bool, str, str, str]:
        try:
            api = subprocess.run(
                ["docker", "version", "--format", "{{.Server.APIVersion}}"],
                capture_output=True, text=True, check=False)
            info = subprocess.run(
                ["docker", "info", "--format", "{{.OSType}}"],
                capture_output=True, text=True, check=False)
        except OSError as exc:
            return (False, "", "", str(exc))
        if api.returncode != 0:
            return (False, "", "", (api.stderr or "daemon unreachable").strip())
        if info.returncode != 0:
            return (False, api.stdout.strip(), "",
                    (info.stderr or "daemon unreachable").strip())
        return (True, api.stdout.strip(), info.stdout.strip(), "")

    def image_ok(self, image: str) -> bool:
        result = subprocess.run(
            ["docker", "manifest", "inspect", image],
            capture_output=True, text=True, check=False)
        return result.returncode == 0

    def git_repo(self, workspace: Path) -> bool:
        return (workspace / ".git").exists()

    def latest_release(self) -> str | None:
        try:
            with urllib.request.urlopen(PYPI_URL, timeout=10) as response:
                payload = json.load(response)
        except Exception:
            return None
        versions = [
            str(candidate) for candidate in (payload.get("releases") or {})
            if _parseable(str(candidate))
        ]
        if versions:
            return max(versions, key=parse_version)
        version = (payload.get("info") or {}).get("version")
        return str(version) if version else None

    def service_reachable(self, host: str, port: int) -> bool:
        try:
            with socket.create_connection((host, port), timeout=2):
                return True
        except OSError:
            return False

    def command_ok(self, command: str) -> bool:
        result = subprocess.run(command, shell=True, capture_output=True,
                                text=True, check=False)
        return result.returncode == 0


def _hard(name: str, ok: bool, message: str = "",
          fix_text: str = "") -> Check:
    return Check(name, "hard", ok, message, fix_text)


def _warn(name: str, ok: bool, message: str = "",
          fix_text: str = "") -> Check:
    return Check(name, "warn", ok, message, fix_text)


def _parseable(text: str) -> bool:
    try:
        parse_version(text)
    except ProfileError:
        return False
    return True


def _api_at_least(api: str) -> bool:
    try:
        parts = tuple(int(p) for p in api.split(".")[:2])
    except ValueError:
        return False
    return parts >= MIN_API


def _env_files(workspace: Path) -> list[str]:
    if not workspace.is_dir():
        return []
    return sorted(
        entry.name for entry in workspace.iterdir()
        if entry.name.startswith(".env") and entry.name not in ENV_WHITELIST)


def _read_compose_services(workspace: Path) -> list[tuple[str, int]]:
    for name in ("docker-compose.yml", "docker-compose.yaml"):
        path = workspace / name
        if not path.is_file():
            continue
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            return []
        found: list[tuple[str, int]] = []
        for service, spec in (data.get("services") or {}).items():
            for port in (spec or {}).get("ports") or []:
                text = str(port).split("/")[0]
                host_port = text.split(":")[-1]
                try:
                    found.append((str(service), int(host_port)))
                except ValueError:
                    continue
        return found
    return []


def collect_checks(profile: Profile | None, pipeline, installed: str,
                   fast: bool, *, workspace: Path | None = None,
                   probes: HostProbes | None = None) -> list[Check]:
    workspace = Path(workspace or Path.cwd()).resolve()
    probes = probes or HostProbes()
    checks: list[Check] = []

    checks.append(_hard(
        "engine",
        bool(installed) and importlib_util.find_spec("agentic_dev_loop")
        is not None,
        "engine package importable" if installed else "engine version unreadable"))

    pin_path = workspace / "agentic-dev-loop" / ".engine-version"
    pin = (pin_path.read_text(encoding="utf-8").strip()
           if pin_path.is_file() else None)
    if pin is None:
        checks.append(_hard(
            "pin", False, f"no engine pin file at {pin_path}",
            "run agentic-dev-loop init (or write agentic-dev-loop/.engine-version)"))
    else:
        checks.append(_hard(
            "pin", pin == installed,
            f"installed {installed}, pin {pin}",
            f'pipx install --force "agentic-dev-loop=={pin}"'))
        latest = probes.latest_release()
        if latest is None:
            checks.append(_hard("pin-latest", True,
                                "latest release not reachable; skipped"))
        else:
            try:
                behind = parse_version(pin) < parse_version(latest)
            except ProfileError:
                behind = False
            checks.append(_hard(
                "pin-latest", not behind,
                f"pin {pin} is behind latest release {latest}",
                f'pipx install --force "agentic-dev-loop=={latest}"'))

    present = probes.docker_present()
    checks.append(_hard(
        "runtime", present,
        "docker is on PATH" if present else "docker is not on PATH",
        "install a compliant container engine (OrbStack, Colima, podman)"))
    ok, api, ostype, error = probes.daemon_info()
    checks.append(_hard("daemon", ok,
                        "daemon reachable" if ok else (error or "daemon unreachable"),
                        "start the container engine"))
    checks.append(_hard(
        "api-version", ok and _api_at_least(api),
        f"engine API {api}" if ok else "engine API unavailable",
        "upgrade to a compliant engine (API >= 1.43)"))
    checks.append(_hard(
        "ostype", ok and ostype.lower() == "linux",
        f"daemon OSType {ostype}" if ok else "daemon OSType unavailable",
        "run a Linux container engine (or WSL2 on Windows)"))

    git_ok = probes.git_repo(workspace)
    checks.append(_hard(
        "workspace", git_ok,
        "workspace is a git repo" if git_ok else "workspace is not a git repo",
        "git init the workspace before running the loop"))

    env_files = _env_files(workspace)
    checks.append(_hard(
        "env-files", not env_files,
        "no .env* files" if not env_files
        else "refused .env* files: " + ", ".join(env_files),
        "move secrets to the host environment"))

    if not fast and pipeline is not None and pipeline.isolation is not None:
        image = pipeline.isolation.image
        if image:
            checks.append(_hard(
                "image", probes.image_ok(image),
                f"image pullable: {image}",
                "publish the image or fix isolation.image"))

    if profile is not None:
        for entry in profile.doctor_checks:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name", "profile-check"))
            command = entry.get("command")
            if command is None:
                continue
            checks.append(_warn(
                f"profile:{name}", probes.command_ok(str(command)),
                f"profile probe failed: {command}",
                "install the missing toolchain"))
        for premise in profile.premise_files:
            exists = (workspace / premise.path).exists()
            checks.append(_warn(
                f"premise:{premise.path}", exists,
                f"missing premise file: {premise.path}",
                "re-run agentic-dev-loop init or restore the file"))

    if pipeline is not None:
        key_env = pipeline.llm.api_key_env
        checks.append(_warn(
            f"llm-key:{key_env}", bool(os.environ.get(key_env)),
            f"LLM key env var not set: {key_env}",
            f"export {key_env}=..."))
        for ref, spec in pipeline.secrets.items():
            checks.append(_warn(
                f"secret:{ref}", bool(os.environ.get(spec.key)),
                f"secret env var not set: {spec.key}",
                f"export {spec.key}=..."))

    if not fast:
        for service, port in _read_compose_services(workspace):
            reachable = probes.service_reachable("127.0.0.1", port)
            checks.append(_warn(
                f"service:{service}", reachable,
                f"compose service {service} unreachable on port {port}",
                "start the service before running the loop"))

    return checks
