"""Unit 10 MCP stdio server: catalog surface and newline JSON-RPC."""

import io
import json
import threading

from agentic_dev_loop.engine.runtimes.catalog import MCP_TOOL_NAMES
from agentic_dev_loop.engine.runtimes.mcp_server import McpServer, McpToolHandler


class FakeBridge:
    def __init__(self):
        self.terminal_calls = []
        self.files = {}
        self.tasks = []

    def run_terminal(self, command, timeout=None):
        self.terminal_calls.append((command, timeout))

        class Outcome:
            def render(self):
                return f"ran {command}"

        return Outcome()

    def view_file(self, path):
        return self.files[path]

    def write_file(self, path, content):
        self.files[path] = content

    def edit_file(self, path, old, new):
        self.files[path] = self.files[path].replace(old, new, 1)

    def insert_file(self, path, line, content):
        self.files[path] = content + self.files[path]

    def task_list(self):
        return self.tasks

    def task_set(self, tasks):
        self.tasks = tasks
        return tasks

    def is_sticky_error(self, exc):
        return False


def _server():
    bridge = FakeBridge()
    return McpServer(McpToolHandler(bridge)), bridge


def test_initialize_reports_tools_capability():
    server, _ = _server()
    response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert response["result"]["capabilities"] == {"tools": {}}
    assert response["result"]["serverInfo"]["name"] == "agentic-dev-loop"


def test_tools_list_is_exactly_the_mcp_catalog():
    server, _ = _server()
    response = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = [tool["name"] for tool in response["result"]["tools"]]
    assert names == list(MCP_TOOL_NAMES)
    assert "finish" not in names
    for tool in response["result"]["tools"]:
        assert tool["inputSchema"]["type"] == "object"


def test_tools_call_terminal_uses_the_bridge():
    server, bridge = _server()
    response = server.handle({
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "terminal", "arguments": {"command": "echo hi"}}})
    assert response["result"]["isError"] is False
    assert bridge.terminal_calls == [("echo hi", None)]
    assert response["result"]["content"][0]["text"] == "ran echo hi"


def test_tools_call_file_editor_round_trip():
    server, bridge = _server()
    server.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                   "params": {"name": "file_editor",
                              "arguments": {"command": "create",
                                            "path": "a.txt",
                                            "file_text": "body"}}})
    response = server.handle({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                              "params": {"name": "file_editor",
                                         "arguments": {"command": "view",
                                                       "path": "a.txt"}}})
    assert response["result"]["content"][0]["text"] == "body"


def test_unknown_tool_is_a_tool_error_not_a_crash():
    server, _ = _server()
    response = server.handle({"jsonrpc": "2.0", "id": 6, "method": "tools/call",
                              "params": {"name": "nope", "arguments": {}}})
    assert response["result"]["isError"] is True


def test_notification_gets_no_reply():
    server, _ = _server()
    assert server.handle({"jsonrpc": "2.0",
                          "method": "notifications/initialized"}) is None


def test_serve_speaks_newline_delimited_json():
    server, bridge = _server()
    stdin = io.StringIO("\n".join([
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                    "params": {"name": "terminal",
                               "arguments": {"command": "pwd"}}}),
    ]) + "\n")
    stdout = io.StringIO()
    server.serve(stdin, stdout)
    replies = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert [reply["id"] for reply in replies] == [1, 2]
    assert bridge.terminal_calls == [("pwd", None)]


def test_serve_runs_on_a_real_stdio_pipe_without_sockets():
    server, bridge = _server()
    reader = io.StringIO(json.dumps(
        {"jsonrpc": "2.0", "id": 9, "method": "tools/list"}) + "\n")
    writer = io.StringIO()
    thread = threading.Thread(target=server.serve, args=(reader, writer))
    thread.start()
    thread.join(5)
    assert not thread.is_alive()
    assert json.loads(writer.getvalue())["id"] == 9


def _real_bridge(tmp_path, fake_sandbox):
    from agentic_dev_loop.core.config import PipelineConfig
    from agentic_dev_loop.engine.runtimes.bridge import SandboxToolBridge
    from agentic_dev_loop.engine.runtimes.protocol import RuntimeContext

    cfg = PipelineConfig.model_validate({
        "name": "pipe", "provider": "copilot", "copilot": {"auth": "device"},
        "agents_dir": "agents", "lockfiles": ["uv.lock"],
        "steps": [{"name": "dev", "agent": "dev", "on_pass": "success"}],
    })
    ctx = RuntimeContext(
        cfg=cfg, workspace=tmp_path, state_dir=tmp_path / ".pr",
        agents_dir=tmp_path / "agents", agent_name="dev", max_iterations=10,
        sandbox=fake_sandbox, sandbox_cwd=tmp_path, terminal_timeout=5)
    return SandboxToolBridge(ctx)


def test_sticky_infra_error_escapes_the_mcp_tool_call(tmp_path, fake_sandbox):
    """030: a dead sandbox must fail the run, never become a retryable tool
    error the harness can keep churning on (BR-10.8 / NFR-10.3)."""
    import pytest

    from agentic_dev_loop.engine.isolation import SandboxError

    error = SandboxError("AE-SBX-019: daemon lost")
    fake_sandbox.infra_error = error

    def boom(request):
        raise error

    fake_sandbox.script(boom)
    server = McpServer(McpToolHandler(_real_bridge(tmp_path, fake_sandbox)))
    with pytest.raises(SandboxError) as excinfo:
        server.handle({"jsonrpc": "2.0", "id": 10, "method": "tools/call",
                       "params": {"name": "terminal",
                                  "arguments": {"command": "echo hi"}}})
    assert excinfo.value is error


def test_non_sticky_sandbox_error_stays_a_tool_error(tmp_path, fake_sandbox):
    from agentic_dev_loop.engine.isolation import SandboxError

    def boom(request):
        raise SandboxError("AE-SBX-099: other")

    fake_sandbox.script(boom)
    server = McpServer(McpToolHandler(_real_bridge(tmp_path, fake_sandbox)))
    response = server.handle({"jsonrpc": "2.0", "id": 11, "method": "tools/call",
                              "params": {"name": "terminal",
                                         "arguments": {"command": "echo hi"}}})
    assert response["result"]["isError"] is False
    assert "AE-SBX-099" in response["result"]["content"][0]["text"]
