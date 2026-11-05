"""Engine-internal MCP server (unit 10).

JSON-RPC over stdio, stdlib only: ``initialize``, ``tools/list`` and
``tools/call``. It binds no socket and holds no engine state; every call is
handed to a ``SandboxToolBridge``-shaped handler. The MCP surface is exactly
the catalog's three execution tools (``finish`` stays harness-native).
"""

from __future__ import annotations

import json
import sys
from typing import IO, Any

from .catalog import MCP_TOOLS
from .errors import RuntimeSetupError

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "agentic-dev-loop"
SERVER_VERSION = "0.1.0"


class McpToolHandler:
    """Thin adapter from MCP tool calls to the sandbox bridge."""

    def __init__(self, bridge) -> None:
        self._bridge = bridge

    def call(self, name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        try:
            return self._dispatch(name, arguments), False
        except Exception as exc:
            if self._bridge.is_sticky_error(exc):
                raise
            return f"{type(exc).__name__}: {exc}", True

    def _dispatch(self, name: str, arguments: dict[str, Any]) -> str:
        if name == "terminal":
            outcome = self._bridge.run_terminal(
                str(arguments["command"]), arguments.get("timeout"))
            return outcome.render()
        if name == "file_editor":
            return self._file_editor(arguments)
        if name == "task_tracker":
            return self._task_tracker(arguments)
        raise RuntimeSetupError(f"unknown MCP tool {name!r}")

    def _file_editor(self, arguments: dict[str, Any]) -> str:
        command = arguments.get("command", "view")
        path = str(arguments["path"])
        if command == "view":
            return self._bridge.view_file(path)
        if command == "create":
            self._bridge.write_file(path, str(arguments.get("file_text", "")))
            return f"created {path}"
        if command == "str_replace":
            self._bridge.edit_file(path, str(arguments["old_str"]),
                                   str(arguments.get("new_str", "")))
            return f"edited {path}"
        if command == "insert":
            self._bridge.insert_file(path, int(arguments.get("insert_line", 0)),
                                     str(arguments.get("new_str", "")))
            return f"edited {path}"
        raise ValueError(f"unsupported file_editor command {command!r}")

    def _task_tracker(self, arguments: dict[str, Any]) -> str:
        command = arguments.get("command", "view")
        if command == "view":
            return json.dumps(self._bridge.task_list())
        if command == "plan":
            tasks = self._bridge.task_set(list(arguments.get("task_list", [])))
            return json.dumps(tasks)
        raise ValueError(f"unsupported task_tracker command {command!r}")


class McpServer:
    """One stdio JSON-RPC server bound to a tool handler."""

    def __init__(self, handler: McpToolHandler | Any,
                 version: str = SERVER_VERSION) -> None:
        self._handler = handler
        self._version = version

    def handle(self, message: dict) -> dict | None:
        method = message.get("method")
        request_id = message.get("id")
        params = message.get("params") or {}
        if method == "initialize":
            result = {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME,
                               "version": self._version},
            }
            return self._reply(request_id, result)
        if method in ("notifications/initialized", "notifications/cancelled"):
            return None
        if method == "ping":
            return self._reply(request_id, {})
        if method == "tools/list":
            return self._reply(request_id, {"tools": [
                {"name": spec.name, "description": spec.description,
                 "inputSchema": spec.input_schema}
                for spec in MCP_TOOLS]})
        if method == "tools/call":
            name = params.get("name", "")
            arguments = params.get("arguments") or {}
            text, is_error = self._handler.call(name, arguments)
            return self._reply(request_id, {
                "content": [{"type": "text", "text": text}],
                "isError": is_error,
            })
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": f"method not found: {method}"},
        }

    @staticmethod
    def _reply(request_id, result) -> dict:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def serve(self, reader: IO[str], writer: IO[str]) -> None:
        """Serve newline-delimited JSON-RPC until EOF."""
        for line in reader:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                response = {
                    "jsonrpc": "2.0", "id": None,
                    "error": {"code": -32700, "message": "parse error"},
                }
            else:
                response = self.handle(message)
            if response is not None:
                writer.write(json.dumps(response) + chr(10))
                writer.flush()


class InProcessMcpServer:
    """The same catalog surface as an in-process object (MCP-only harnesses
    that accept a server object rather than spawning a child)."""

    name = SERVER_NAME
    version = SERVER_VERSION

    def __init__(self, bridge) -> None:
        self._handler = McpToolHandler(bridge)

    def list_tools(self) -> list[dict]:
        return [{"name": spec.name, "description": spec.description,
                 "inputSchema": spec.input_schema} for spec in MCP_TOOLS]

    async def call_tool(self, name: str, arguments: dict) -> dict:
        text, is_error = self._handler.call(name, arguments)
        return {"content": [{"type": "text", "text": text}],
                "isError": is_error}


def serve_stdio(bridge, stdin: IO[str] | None = None,
                stdout: IO[str] | None = None) -> None:
    server = McpServer(McpToolHandler(bridge))
    server.serve(stdin or sys.stdin, stdout or sys.stdout)


def build_sdk_mcp_server(bridge, name: str = SERVER_NAME):
    """Bind the catalog to an SDK-native in-process MCP server."""
    try:
        from claude_agent_sdk import create_sdk_mcp_server, tool
    except ImportError as exc:  # pragma: no cover - exercised with extras
        raise RuntimeSetupError(
            "claude-agent-sdk does not expose an in-process MCP server; "
            "install a supported version with "
            + 'pip ' 'install "agentic-dev-loop[claude]"') from exc

    handler = McpToolHandler(bridge)

    def _wrap(spec):
        async def _call(arguments):
            text, is_error = handler.call(spec.name, arguments)
            return {"content": [{"type": "text", "text": text}],
                    "isError": is_error}

        _call.__name__ = spec.name
        return tool(spec.name, spec.description, spec.input_schema)(_call)

    return create_sdk_mcp_server(name=name, version=SERVER_VERSION,
                                 tools=[_wrap(spec) for spec in MCP_TOOLS])
