#!/usr/bin/env python3
"""Smoke-test the agentic-dev-loop engine FROM SOURCE against a smoke plan.

Runs the engine straight from this repository's source tree (PYTHONPATH=src,
no wheel, no pip install) in a DISPOSABLE GIT WORKTREE of this repo, so the
workspace is never the source tree (the host-boundary check stays green and
the main checkout is never touched). The run is driven by a config directory:

    <config-dir>/
        pipeline.yaml          pipeline template (deps -> dev -> test)
        agents/dev.agent.md    the agent definition used by the dev step
        plan.md                optional plan document (fed as an additional
                               file when present)

Provider and plan are independent: the --provider argument injects the
matching llm block (PROVIDER_BLOCKS) into the config dir's pipeline
template, which must be provider-free.

The script pre-flights the credentials, creates the worktree, launches the
engine, streams its output, and waits for the engine to exit on its own.
That is the whole job: the smoke test is human-run and human-gated. The
script does not probe endpoints, does not signal the engine, and does not
decide pass or fail - the human reads the streamed output and presses
ctrl+c when they have seen enough. The script exits with the engine's exit
code.

On-demand local tooling: never run in CI.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SRC_DIR = REPO_ROOT / "src"

# provider argument -> the llm block injected into the config dir's template.
# The config dir is provider-free (the provider owns the llm block), so the
# same plan runs against any provider without editing yaml.
#
# Model pins (probed 2026-11-06 against the real endpoints):
# - openhands = DeepSeek: pro deepseek-v4-pro, flash deepseek-flash with
#   reasoning_effort medium (mirrors agentic-dev-loop/pipeline.yaml).
# - copilot = claude-opus-5.5 (pro) / claude-sonnet-5.5 (flash): re-testing
#   the Anthropic models against the post-unit-11 engine. History: these
#   Anthropic pins (claude-opus-5.5 pro / claude-sonnet-5.5 flash) collapsed
#   on agent steps (see planning-docs/059), and
#   kimi-k3 was unusable in agent loops because the Copilot gateway
#   silently drops ARRAY-encoded tool-result content, which the
#   openhands SDK sends by default for kimi (the dev agent worked blind;
#   see the force_string_serializer policy in
#   src/agentic_dev_loop/engine/provider_wire.py, still applied to
#   every Copilot config). Engine-path probe of the new pins
#   (2026-11-06): gpt-4.1 on the chat path makes clean tool calls with
#   string tool content and no reasoning_effort (any effort hard-400s
#   on gpt-4.1), and gpt-5.6-luna routes through the Responses API
#   (SDK auto-detection), accepts xhigh, acts on the review message,
#   and returns a real verdict. gpt-5.6-luna is Responses-API-only:
#   bare "gpt-5.6" is not a tier entry (tier has luna/sol/terra).
#   Rejected on earlier rounds: gpt-4o-mini (Copilot's real chat context
#   is 12288 tokens - the dev agent overran it on the first smoke run),
#   claude-opus-4.8 (Copilot returns two choices on the non-streamed
#   path the engine uses: the litellm-surfaced one has finish_reason
#   tool_calls with tool_calls=None).
# - Copilot pins carry the openai/ prefix on purpose. Probe (2026-11-06):
#   litellm.completion(model="claude-sonnet-5.5", api_base=copilot)
#   failed: litellm routed the bare name through its Anthropic provider
#   path, which speaks Anthropic's wire format (x-api-key) to the
#   gateway and dies with "missing required Authorization header"
#   (observed on the brief step of the first mirror smoke run). The
#   openai/ prefix forces litellm's OpenAI-compatible route:
#   Authorization Bearer plus chat/completions, with the bare model name
#   in the body. The same rule covers the gpt pins here (bare gpt names
#   would skip the Copilot gateway routing).
# - claude = the Anthropic subscription endpoint (api.anthropic.com, no
#   /v1 suffix: litellm appends /v1/messages itself), served by litellm's
#   native Anthropic provider path. Model names are
#   BARE (claude-opus-5-5 / claude-sonnet-5-5): no openai/ prefix, the
#   prefix is Copilot-gateway-specific and bare names are the whole point
#   of this path. The credential is the Claude Code OAuth token
#   (sk-ant-oat01-...), file-backed; litellm auto-detects the token
#   prefix and switches to Bearer auth plus the oauth-2025-04-20 beta
#   with no headers block needed in config. The engine injects the
#   billing-header system block the gateway demands (probe-validated
#   2026-11-13: without it every request 429s) via the provider_wire.py
#   claude-subscription adapter, keyed on this exact base_url. Pins
#   probe-validated on a Pro subscription: opus-5-5 (pro default) and
#   sonnet-5-5 (flash), all reasoning efforts (low/medium/high/xhigh/
#   max) accepted.
PROVIDER_BLOCKS = {
    "openhands": (
        "llm:\n"
        "  model: openai/deepseek-v4-pro\n"
        "  base_url: https://api.deepseek.com\n"
        "  api_key_env: OPENAI_API_KEY\n"
        "llms:\n"
        "  flash:\n"
        "    model: openai/deepseek-flash\n"
        "    base_url: https://api.deepseek.com\n"
        "    api_key_env: OPENAI_API_KEY\n"
        "    reasoning_effort: medium\n"
    ),
    "copilot": (
        "llm:\n"
        "  model: openai/claude-opus-5.5\n"
        "  base_url: https://api.githubcopilot.com\n"
        "  api_key_secret:\n"
        "    from: file\n"
        "    key: ~/.agentic-dev-loop/auth/copilot.json\n"
        "    json_key: access_token\n"
        "  headers:\n"
        "    Copilot-Integration-Id: vscode-chat\n"
        "    Editor-Version: vscode/1.94.0\n"
        "    Openai-Intent: conversation-panel\n"
        "llms:\n"
        "  flash:\n"
        "    model: openai/claude-sonnet-5.5\n"
        "    base_url: https://api.githubcopilot.com\n"
        "    api_key_secret:\n"
        "      from: file\n"
        "      key: ~/.agentic-dev-loop/auth/copilot.json\n"
        "      json_key: access_token\n"
        "    headers:\n"
        "      Copilot-Integration-Id: vscode-chat\n"
        "      Editor-Version: vscode/1.94.0\n"
        "      Openai-Intent: conversation-panel\n"
    ),
    "claude": (
        "llm:\n"
        "  model: claude-opus-5-5\n"
        "  base_url: https://api.anthropic.com\n"
        "  api_key_secret:\n"
        "    from: file\n"
        "    key: ~/.agentic-dev-loop/auth/claude.json\n"
        "    json_key: access_token\n"
        "llms:\n"
        "  flash:\n"
        "    model: claude-sonnet-5-5\n"
        "    base_url: https://api.anthropic.com\n"
        "    api_key_secret:\n"
        "      from: file\n"
        "      key: ~/.agentic-dev-loop/auth/claude.json\n"
        "      json_key: access_token\n"
    ),
}


class SmokePreflightError(RuntimeError):
    """A loud pre-flight failure naming the operator's next command."""


def _secret_block(text: str) -> str | None:
    """The indented body of an ``api_key_secret:`` mapping, if present."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if re.match(r"^\s*api_key_secret:\s*$", line):
            indent = len(line) - len(line.lstrip())
            block = []
            for following in lines[index + 1:]:
                if (following.strip()
                        and len(following) - len(following.lstrip()) <= indent):
                    break
                block.append(following)
            return "\n".join(block)
    return None


def _field(block: str, name: str) -> str | None:
    match = re.search(rf"^\s*{name}:\s*(\S+)\s*$", block, re.MULTILINE)
    return match.group(1).strip("\"'") if match else None


def inject_provider_block(template: str, provider: str) -> str:
    """Inject the provider's llm block into a provider-free template.

    The block lands after the ``name:`` line (or at the top when the
    template has none). A template that still carries its own ``llm:``
    block is a config error: the provider argument owns the llm block, and
    silently picking a winner would hide the stale coupling.
    """
    if re.search(r"^\s*llm:\s*(#.*)?$", template, re.MULTILINE):
        raise SmokePreflightError(
            "the config dir's pipeline.yaml still carries an 'llm:' block - "
            "the --provider argument owns the llm block; remove the 'llm:' "
            "block from the template")
    block = PROVIDER_BLOCKS[provider]
    match = re.search(r"^name:\s*\S+.*$", template, re.MULTILINE)
    if match is None:
        return block + template
    return template[:match.end()] + "\n" + block + template[match.end():]


def preflight_llm(text: str,
                  environ: dict[str, str] | None = None,
                  provider: str | None = None) -> None:
    """Fail in seconds, before a worktree and deps build, when the pipeline
    template's llm credentials are absent. Regex-based and stdlib-only: the
    pipeline yaml is a template, not a loaded config."""
    environ = os.environ if environ is None else environ
    for match in re.finditer(r"^\s*api_key_env:\s*(\S+)\s*$", text,
                             re.MULTILINE):
        variable = match.group(1).strip("\"'")
        if variable not in environ:
            raise SmokePreflightError(
                f"llm api_key_env {variable} is not set - run "
                f"`export {variable}=...` before this smoke test")
    block = _secret_block(text)
    if block is not None and _field(block, "from") == "file":
        key = _field(block, "key")
        if not key:
            raise SmokePreflightError(
                "llm api_key_secret has no 'key' file path")
        path = Path(key).expanduser()
        if not path.is_file():
            hint = {
                "copilot": "run `agentic-dev-loop copilot-login` first",
                "claude": "run `agentic-dev-loop claude-login` first",
            }.get(provider or "", "create the provider's credential file")
            raise SmokePreflightError(
                f"llm api_key_secret file not found: {path} - {hint}")


def _engine_auth():
    if str(SRC_DIR) not in sys.path:
        sys.path.insert(0, str(SRC_DIR))
    from agentic_dev_loop.engine import copilot_auth
    return copilot_auth


def preflight_copilot_model(text: str, *, token_reader=None,
                            models_lister=None) -> None:
    """For a file-backed Copilot config, probe /models and confirm every
    pinned model (default llm block plus named profiles) exists for this
    token's tier. Non-Copilot configs are a no-op."""
    block = _secret_block(text)
    if block is None or _field(block, "from") != "file":
        return
    key = _field(block, "key")
    if not key:
        return
    pinned = {match.group(1).strip("\"'")
              for match in re.finditer(r"^\s*model:\s*(\S+)\s*$", text,
                                       re.MULTILINE)}
    if not pinned:
        return
    # The tier list holds bare model names; a litellm provider prefix
    # ("openai/claude-opus-5.5") is a wire-format routing hint, not part
    # of the model's name, so strip it before comparing.
    bare = {name.split("/", 1)[1] if "/" in name else name
            for name in pinned}
    module = _engine_auth()
    token_reader = token_reader or module.read_token
    models_lister = models_lister or module.list_copilot_models
    try:
        models = models_lister(token_reader(Path(key).expanduser()))
    except module.CopilotAuthError as e:
        raise SmokePreflightError(str(e)) from e
    missing = sorted(model for model in bare if model not in models)
    if missing:
        raise SmokePreflightError(
            f"model(s) {', '.join(repr(m) for m in missing)} not in this "
            f"token's Copilot model list ({', '.join(models)}) - run "
            "`agentic-dev-loop copilot-models` and pin available models")


def free_port() -> int:
    """A port that is free right now (best effort, race-free enough)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def fail(message: str, code: int = 2) -> None:
    print(f"run_smoke: {message}", file=sys.stderr)
    sys.exit(code)


def check_assets_clean(config_dir: Path) -> None:
    """The smoke assets must be committed (tooling-integrity refuses dirty
    tooling paths; a fresh worktree only carries committed files)."""
    result = subprocess.run(
        ["git", "status", "--porcelain", "--", str(config_dir)],
        cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        fail(f"git status failed: {result.stderr.strip()}", 1)
    if result.stdout.strip():
        fail(
            f"smoke assets are dirty relative to HEAD - commit them first "
            f"(the engine refuses dirty tooling paths and a worktree only "
            f"carries committed files):\n" + result.stdout, 1)


def create_worktree(worktree_dir: Path) -> None:
    worktree_dir.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["git", "worktree", "add", "--detach", str(worktree_dir)],
        cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        fail(f"git worktree add failed: {result.stderr.strip()}", 1)


def remove_worktree(worktree_dir: Path) -> None:
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(worktree_dir)],
        cwd=REPO_ROOT, capture_output=True, text=True, check=False)


def build_pipeline(worktree: Path, config_dir: Path, template_text: str,
                   tmpdir: Path) -> Path:
    """Write the template into a temp dir with absolute paths.

    agents_dir and additional_files become ABSOLUTE worktree paths (the
    loader resolves pipeline-dir-relative paths, and absolute paths pass
    through; they must resolve inside the workspace and be clean). The
    template already carries the injected provider block.
    """
    text = template_text
    agents_in_worktree = worktree / config_dir.relative_to(REPO_ROOT) / "agents"
    text = text.replace(
        "agents_dir: agents\n",
        f"agents_dir: {json.dumps(str(agents_in_worktree))}\n",
        1)
    plan_in_worktree = worktree / config_dir.relative_to(REPO_ROOT) / "plan.md"
    if (config_dir / "plan.md").is_file():
        text = text.replace(
            "additional_files: []\n",
            f"additional_files: [{json.dumps(str(plan_in_worktree))}]\n",
            1)
    pipeline = tmpdir / "pipeline.yaml"
    pipeline.write_text(text, encoding="utf-8")
    return pipeline


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Smoke-test the engine from source against a smoke plan.")
    parser.add_argument(
        "config_dir", type=Path,
        help="smoke config directory containing pipeline.yaml and "
             "agents/dev.agent.md (and optionally plan.md, fed to the run "
             "as an additional file); the pipeline.yaml must be "
             "provider-free (the --provider argument owns the llm block)")
    parser.add_argument(
        "--provider", required=True, choices=sorted(PROVIDER_BLOCKS),
        help="llm backend for this run: the matching block from "
             "PROVIDER_BLOCKS is injected into the config dir's pipeline "
             "template")
    parser.add_argument(
        "--worktree-dir", type=Path, default=None,
        help="worktree path (default: ~/.agentic-dev-loop/smoke/worktrees/"
             "<config-dir-name>-<timestamp>)")
    parser.add_argument(
        "--keep-worktree", action="store_true",
        help="keep the worktree after the run for inspection")
    parser.add_argument(
        "--viz-port", type=int, default=None,
        help="viz port (default: a free port chosen automatically)")
    parser.add_argument(
        "--engine-python", type=Path,
        default=REPO_ROOT / ".venv" / "bin" / "python",
        help="python interpreter that can import the engine's dependencies "
             "(default: <repo>/.venv/bin/python)")
    args = parser.parse_args()

    engine_python = args.engine_python
    if not engine_python.is_file():
        fail(f"engine python not found: {engine_python} "
             "(build the repo venv or pass --engine-python)")
    config_dir = args.config_dir.expanduser().resolve()
    if not config_dir.is_dir():
        fail(f"config directory not found: {config_dir}")
    if not (config_dir / "pipeline.yaml").is_file():
        fail(f"pipeline template not found: {config_dir / 'pipeline.yaml'}")
    if not (config_dir / "agents" / "dev.agent.md").is_file():
        fail(f"dev agent not found: {config_dir / 'agents' / 'dev.agent.md'}")

    try:
        template = (config_dir / "pipeline.yaml").read_text(encoding="utf-8")
        text = inject_provider_block(template, args.provider)
        preflight_llm(text, provider=args.provider)
        if args.provider == "copilot":
            # The Copilot tier check probes GitHub's /models with the
            # Copilot token; every other provider skips it.
            preflight_copilot_model(text)
    except SmokePreflightError as e:
        fail(str(e))

    check_assets_clean(config_dir)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    worktree = args.worktree_dir or (
        Path("~/.agentic-dev-loop/smoke/worktrees").expanduser()
        / f"{config_dir.name}-{stamp}")
    if worktree.exists():
        fail(f"worktree already exists: {worktree}")

    port = args.viz_port or free_port()

    print(f"[smoke] config dir: {config_dir}")
    print(f"[smoke] provider: {args.provider}")
    print(f"[smoke] worktree: {worktree}")
    print(f"[smoke] viz port: {port}")
    print("[smoke] creating worktree from HEAD...")
    create_worktree(worktree)
    try:
        tmpdir = Path(tempfile.mkdtemp(prefix="run-smoke-"))
        pipeline = build_pipeline(worktree, config_dir, text, tmpdir)
        print(f"[smoke] pipeline: {pipeline}")

        bootstrap = (
            "import sys\n"
            "from agentic_dev_loop.engine.cli import main as cli_main\n"
            "sys.exit(cli_main(sys.argv[1:]))\n")
        cmd = [
            str(engine_python), "-c", bootstrap,
            str(pipeline), "--viz-port", str(port),
        ]
        env = dict(os.environ)
        env["PYTHONPATH"] = str(SRC_DIR)
        print(f"[smoke] running engine from source (PYTHONPATH={SRC_DIR}) "
              f"with cwd={worktree}")

        proc = subprocess.Popen(
            cmd, cwd=str(worktree), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1)

        def pump() -> None:
            assert proc.stdout is not None
            for line in iter(proc.stdout.readline, ""):
                print(line, end="", flush=True)

        reader = threading.Thread(target=pump, daemon=True)
        reader.start()

        # The script's whole job: stream the run and let the engine exit on
        # its own. Human-run, human-gated: no probes, no signals, no verdict.
        return proc.wait()
    finally:
        if args.keep_worktree:
            print(f"[smoke] keeping worktree: {worktree}")
        else:
            remove_worktree(worktree)
            print("[smoke] worktree removed")


if __name__ == "__main__":
    sys.exit(main())
