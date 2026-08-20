#!/usr/bin/env python3
"""MCP Server - ssh-alias-mcp

MCP stdio protocol wrapper around ssh_client.py.
"""
import io
import json
import re
import sys
from dataclasses import dataclass
from hashlib import sha1
from pathlib import Path
from typing import Callable, Dict, Tuple

# Force UTF-8 output on Windows. stdout must contain only MCP JSON messages.
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from ssh_client import pool


SERVER_INFO = {
    "name": "ssh-alias-mcp",
    "title": "SSH Alias MCP",
    "version": "1.2.0",
    "description": "Run configured SSH commands, scripts, aliases, uploads, and downloads.",
}
SUPPORTED_PROTOCOL_VERSIONS = [
    "2025-11-25",
    "2025-06-18",
    "2025-03-26",
    "2024-11-05",
]
LATEST_PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[0]
MCP_INSTRUCTIONS = (
    "Use ssh_list_servers first to discover configured servers. "
    "Use ssh_open_config when the user wants to manage server settings in the UI. "
    "Use ssh_list_aliases before ssh_run_alias unless you already know the alias name. "
    "Server config create, update, copy, and delete tools modify local YAML files. "
    "Commands, uploads, downloads, and aliases can modify remote systems."
)
TOOL_NAME_MAX_LENGTH = 128
SECRET_FIELDS = {
    "password",
    "key_password",
    "passphrase",
    "private_key",
    "private_key_path",
    "proxy_password",
    "sudo_password",
}
CONFIG_UI_URI = "ui://ssh-alias-mcp/config-v1.html"
CONFIG_UI_MIME_TYPE = "text/html;profile=mcp-app"
CONFIG_UI_PATH = Path(__file__).resolve().parent / "ui" / "config.html"
CONFIG_UI_TOOL_META = {
    "ui": {
        "resourceUri": CONFIG_UI_URI,
        "visibility": ["model", "app"],
    },
    "openai/outputTemplate": CONFIG_UI_URI,
    "openai/widgetAccessible": True,
    "openai/toolInvocation/invoking": "正在打开配置",
    "openai/toolInvocation/invoked": "配置已打开",
}
APP_ONLY_TOOL_META = {
    "ui": {"visibility": ["app"]},
    "openai/visibility": "private",
    "openai/widgetAccessible": True,
}
APP_CALLABLE_TOOL_META = {
    "ui": {"visibility": ["model", "app"]},
    "openai/widgetAccessible": True,
}
SERVER_LIST_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "count": {"type": "integer"},
        "servers": {
            "type": "array",
            "items": {"type": "object", "additionalProperties": True},
        },
    },
    "required": ["count", "servers"],
    "additionalProperties": False,
}
CONFIG_VIEW_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "view": {"type": "string", "enum": ["server-config"]},
        "count": {"type": "integer"},
        "servers": {
            "type": "array",
            "items": {"type": "object", "additionalProperties": True},
        },
    },
    "required": ["view", "count", "servers"],
    "additionalProperties": False,
}
SERVER_CONFIG_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "server": {"type": "object", "additionalProperties": True},
        "secrets": {
            "type": "object",
            "properties": {
                "password": {"type": "boolean"},
                "key_password": {"type": "boolean"},
                "sudo_password": {"type": "boolean"},
            },
            "required": ["password", "key_password", "sudo_password"],
            "additionalProperties": False,
        },
        "inherited": {"type": "boolean"},
    },
    "required": ["name", "server", "secrets", "inherited"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class ArgSpec:
    name: str
    json_type: str
    description: str
    required: bool = False
    default: object = None

    def schema(self) -> dict:
        data = {"type": self.json_type, "description": self.description}
        if not self.required and self.default is not None:
            data["default"] = self.default
        return data


@dataclass(frozen=True)
class ToolSpec:
    name: str
    title: str
    description: str
    args: Tuple[ArgSpec, ...]
    annotations: dict
    handler: Callable[[dict], dict]
    meta: dict = None
    output_schema: dict = None

    def tool(self) -> dict:
        input_schema = {
            "type": "object",
            "additionalProperties": False,
        }
        if self.args:
            input_schema["properties"] = {arg.name: arg.schema() for arg in self.args}
            required = [arg.name for arg in self.args if arg.required]
            if required:
                input_schema["required"] = required
        descriptor = {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": input_schema,
            "annotations": self.annotations,
        }
        if self.meta:
            descriptor["_meta"] = self.meta
        if self.output_schema:
            descriptor["outputSchema"] = self.output_schema
        return descriptor


class McpProtocolError(Exception):
    def __init__(self, code: int, message: str, data=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def mcp_result(content: list, is_error: bool = False, structured_content=None) -> dict:
    result = {"content": content, "isError": is_error}
    if structured_content is not None:
        result["structuredContent"] = structured_content
    return result


def mcp_text(text: str, is_error: bool = False) -> dict:
    return mcp_result([{"type": "text", "text": text}], is_error)


def jsonrpc_error(req_id, code: int, message: str, data=None) -> dict:
    error = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": error}


def jsonrpc_result(req_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _require_object(value, name: str) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise McpProtocolError(-32602, f"{name} must be an object")
    return value


def _validate_tool_args(args: dict, specs: Tuple[ArgSpec, ...]) -> dict:
    allowed = {spec.name for spec in specs}
    unknown = sorted(set(args) - allowed)
    if unknown:
        raise McpProtocolError(-32602, f"Unknown argument(s): {', '.join(unknown)}")
    values = {}
    for spec in specs:
        value = args.get(spec.name, spec.default)
        if spec.required and (value is None or value == ""):
            raise McpProtocolError(
                -32602,
                f"Missing or invalid required {spec.json_type} argument: {spec.name}",
            )
        if value is None:
            values[spec.name] = None
            continue
        if spec.json_type == "string" and not isinstance(value, str):
            raise McpProtocolError(-32602, f"Invalid string argument: {spec.name}")
        if spec.json_type == "string" and spec.required and not value:
            raise McpProtocolError(
                -32602,
                f"Missing or invalid required string argument: {spec.name}",
            )
        if spec.json_type == "boolean" and not isinstance(value, bool):
            raise McpProtocolError(-32602, f"Invalid boolean argument: {spec.name}")
        if spec.json_type == "object" and not isinstance(value, dict):
            raise McpProtocolError(-32602, f"Invalid object argument: {spec.name}")
        if spec.json_type == "number":
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise McpProtocolError(-32602, f"Invalid number argument: {spec.name}")
            if value <= 0:
                raise McpProtocolError(-32602, f"{spec.name} must be greater than 0")
        values[spec.name] = value
    return values


def _sanitize_tool_part(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._-")
    return value or "unnamed"


def _alias_tool_base(server: str, alias_name: str) -> str:
    return f"ssh_alias.{_sanitize_tool_part(server)}.{_sanitize_tool_part(alias_name)}"


def _unique_alias_tool_name(server: str, alias_name: str, used: set) -> str:
    base = _alias_tool_base(server, alias_name)
    tool_name = base[:TOOL_NAME_MAX_LENGTH]
    if tool_name not in used:
        used.add(tool_name)
        return tool_name

    digest = sha1(f"{server}\0{alias_name}".encode("utf-8")).hexdigest()[:8]
    suffix = f".{digest}"
    tool_name = base[:TOOL_NAME_MAX_LENGTH - len(suffix)] + suffix
    if tool_name not in used:
        used.add(tool_name)
        return tool_name

    counter = 2
    while True:
        suffix = f".{digest}.{counter}"
        tool_name = base[:TOOL_NAME_MAX_LENGTH - len(suffix)] + suffix
        if tool_name not in used:
            used.add(tool_name)
            return tool_name
        counter += 1


def _alias_tool_specs() -> Dict[str, ToolSpec]:
    specs = {}
    used = set()
    for sv in pool.list_servers():
        if sv.get("error"):
            continue
        server = sv["name"]
        conn = pool.get(server)
        for alias in conn.list_aliases():
            alias_name = alias["name"]
            tool_name = _unique_alias_tool_name(server, alias_name, used)
            desc = alias.get("desc", "")
            if "inline" in alias:
                script_hint = f"(runs inline: {alias['inline']})"
            else:
                script_hint = f"(runs script: {alias.get('script', '')})"
            specs[tool_name] = ToolSpec(
                name=tool_name,
                title=f"{server}: {alias_name}",
                description=f"[{server}] {desc} {script_hint}".strip(),
                args=(),
                annotations={"readOnlyHint": False, "destructiveHint": True},
                handler=_run_alias_handler(server, alias_name),
            )
    return specs


def _run_alias_handler(server: str, alias_name: str) -> Callable[[dict], dict]:
    def handler(args: dict) -> dict:
        conn = pool.get(server)
        return _with_execution_summary(
            conn.run_alias(alias_name),
            _alias_execution_summary(conn, server, alias_name),
        )

    return handler


def _alias_execution_summary(conn, server: str, alias_name: str) -> list:
    lines = [
        f"Tool: ssh_alias.{server}.{alias_name}",
        f"Server: {server}",
        f"Alias: {alias_name}",
    ]
    for alias in getattr(conn, "aliases", []):
        if alias.get("name") != alias_name:
            continue
        if "inline" in alias:
            lines.append(f"Inline command: {alias['inline']}")
        if "script" in alias:
            lines.append(f"Script: {alias['script']}")
        lines.append(f"Timeout: {alias.get('timeout', 300)}")
        lines.append(f"Sudo: {alias.get('sudo', False)}")
        break
    return lines


def _raw_output(result: dict) -> dict:
    lines = []
    if result.get("stdout"):
        lines.append(result["stdout"].rstrip())
    if result.get("stderr"):
        lines.append("STDERR:\n" + result["stderr"].rstrip())
    if result.get("code", 0) != 0:
        lines.append(f"\nExited with code {result['code']}")
    text = "\n".join(lines) if lines else "(empty output)"
    return mcp_text(text, result.get("code", 0) != 0)


def _with_execution_summary(result: dict, lines: list) -> dict:
    output = _raw_output(result)
    header = "\n".join(lines)
    text = output["content"][0]["text"]
    output["content"][0]["text"] = f"{header}\n\n{text}"
    return output


def _json_text(payload: dict, structured: bool = False) -> dict:
    content = [{"type": "text", "text": json.dumps(payload, indent=2, ensure_ascii=False)}]
    if structured:
        return mcp_result(content, structured_content=payload)
    return mcp_result(content)


def _server_conn(args: dict):
    return pool.get(args["server"])


def _handle_ssh_run(args: dict) -> dict:
    return _with_execution_summary(
        _server_conn(args).run(
            args["command"],
            timeout=args["timeout"],
            sudo=args["sudo"],
        ),
        [
            f"Tool: ssh_run",
            f"Server: {args['server']}",
            f"Command: {args['command']}",
            f"Timeout: {args['timeout']}",
            f"Sudo: {args['sudo']}",
        ],
    )


def _handle_ssh_run_script(args: dict) -> dict:
    return _with_execution_summary(
        _server_conn(args).run_script(
            args["script_name"],
            timeout=args["timeout"],
            sudo=args["sudo"],
        ),
        [
            f"Tool: ssh_run_script",
            f"Server: {args['server']}",
            f"Script: {args['script_name']}",
            f"Timeout: {args['timeout']}",
            f"Sudo: {args['sudo']}",
        ],
    )


def _handle_ssh_upload_script(args: dict) -> dict:
    return _with_execution_summary(
        _server_conn(args).upload_script(
            args["local_path"],
            args["script_name"],
            args["run_immediately"],
            args["timeout"],
            args["overwrite"],
            args["sudo"],
        ),
        [
            f"Tool: ssh_upload_script",
            f"Server: {args['server']}",
            f"Local path: {args['local_path']}",
            f"Script name: {args['script_name'] or '(same as local filename)'}",
            f"Run immediately: {args['run_immediately']}",
            f"Timeout: {args['timeout']}",
            f"Overwrite: {args['overwrite']}",
            f"Sudo: {args['sudo']}",
        ],
    )


def _handle_ssh_list_scripts(args: dict) -> dict:
    return _with_execution_summary(
        _server_conn(args).list_scripts(sudo=args["sudo"]),
        [
            f"Tool: ssh_list_scripts",
            f"Server: {args['server']}",
            f"Sudo: {args['sudo']}",
        ],
    )


def _handle_ssh_list_servers(args: dict) -> dict:
    servers = pool.list_servers()
    return _json_text({"count": len(servers), "servers": servers}, structured=True)


def _handle_ssh_open_config(args: dict) -> dict:
    servers = pool.list_servers()
    return _json_text({
        "view": "server-config",
        "count": len(servers),
        "servers": servers,
    }, structured=True)


def _handle_ssh_get_server_config(args: dict) -> dict:
    return _json_text(pool.get_server_config(args["server"]), structured=True)


def _handle_ssh_create_server(args: dict) -> dict:
    return _json_text(pool.create_server(args["server"], args["config"]))


def _handle_ssh_update_server(args: dict) -> dict:
    return _json_text(
        pool.update_server(args["server"], args["config"], replace=args["replace"])
    )


def _handle_ssh_copy_server(args: dict) -> dict:
    return _json_text(
        pool.copy_server(args["source_server"], args["target_server"])
    )


def _handle_ssh_delete_server(args: dict) -> dict:
    return _json_text(pool.delete_server(args["server"]))


def _handle_ssh_download(args: dict) -> dict:
    return _with_execution_summary(
        _server_conn(args).download(
            args["remote_path"],
            args["local_path"],
            timeout=args["timeout"],
            pattern=args["pattern"],
            overwrite=args["overwrite"],
            sudo=args["sudo"],
        ),
        [
            f"Tool: ssh_download",
            f"Server: {args['server']}",
            f"Remote path: {args['remote_path']}",
            f"Local path: {args['local_path']}",
            f"Pattern: {args['pattern'] or '(none)'}",
            f"Timeout: {args['timeout']}",
            f"Overwrite: {args['overwrite']}",
            f"Sudo: {args['sudo']}",
        ],
    )


def _handle_ssh_upload_all_scripts(args: dict) -> dict:
    return _with_execution_summary(
        _server_conn(args).upload_all_scripts(sudo=args["sudo"]),
        [
            f"Tool: ssh_upload_all_scripts",
            f"Server: {args['server']}",
            f"Sudo: {args['sudo']}",
        ],
    )


def _handle_ssh_run_alias(args: dict) -> dict:
    conn = _server_conn(args)
    return _with_execution_summary(
        conn.run_alias(args["alias_name"]),
        _alias_execution_summary(conn, args["server"], args["alias_name"]),
    )


def _handle_ssh_list_aliases(args: dict) -> dict:
    aliases = _server_conn(args).list_aliases()
    return _json_text({
        "tool": "ssh_list_aliases",
        "server": args["server"],
        "count": len(aliases),
        "aliases": aliases,
    })


def _tool_args_summary(name: str, args: dict) -> str:
    return "\n".join([
        f"Tool: {name}",
        "Arguments:",
        json.dumps(_redact_secrets(args), indent=2, ensure_ascii=False),
    ])


def _redact_secrets(value):
    if isinstance(value, dict):
        return {
            key: "***REDACTED***" if key.lower() in SECRET_FIELDS else _redact_secrets(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_secrets(item) for item in value]
    return value


def _arg(name: str, json_type: str, description: str, required: bool = False, default=None) -> ArgSpec:
    return ArgSpec(name, json_type, description, required, default)


SERVER_ARG = _arg("server", "string", "Server name", required=True)
SUDO_ARG = _arg("sudo", "boolean", "Run as root via sudo", default=False)
TIMEOUT_300_ARG = _arg("timeout", "number", "Timeout in seconds", default=300)


STATIC_TOOLS = {
    "ssh_open_config": ToolSpec(
        name="ssh_open_config",
        title="配置 SSH 服务器",
        description="打开服务器配置界面。",
        args=(),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "openWorldHint": False,
        },
        handler=_handle_ssh_open_config,
        meta=CONFIG_UI_TOOL_META,
        output_schema=CONFIG_VIEW_OUTPUT_SCHEMA,
    ),
    "ssh_get_server_config": ToolSpec(
        name="ssh_get_server_config",
        title="读取 SSH 服务器配置",
        description="读取可编辑配置。不会返回密码。",
        args=(SERVER_ARG,),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "openWorldHint": False,
        },
        handler=_handle_ssh_get_server_config,
        meta=APP_ONLY_TOOL_META,
        output_schema=SERVER_CONFIG_OUTPUT_SCHEMA,
    ),
    "ssh_ui_create_server": ToolSpec(
        name="ssh_ui_create_server",
        title="保存新服务器",
        description="从配置界面保存新服务器。",
        args=(
            SERVER_ARG,
            _arg("config", "object", "Complete server configuration object", required=True),
        ),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "openWorldHint": False,
        },
        handler=_handle_ssh_create_server,
        meta=APP_ONLY_TOOL_META,
    ),
    "ssh_ui_update_server": ToolSpec(
        name="ssh_ui_update_server",
        title="保存服务器修改",
        description="从配置界面保存服务器修改。",
        args=(
            SERVER_ARG,
            _arg("config", "object", "Configuration patch or complete replacement", required=True),
            _arg("replace", "boolean", "Replace the full config instead of merging", default=False),
        ),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "openWorldHint": False,
        },
        handler=_handle_ssh_update_server,
        meta=APP_ONLY_TOOL_META,
    ),
    "ssh_ui_delete_server": ToolSpec(
        name="ssh_ui_delete_server",
        title="删除服务器",
        description="从配置界面删除服务器。",
        args=(SERVER_ARG,),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "openWorldHint": False,
        },
        handler=_handle_ssh_delete_server,
        meta=APP_ONLY_TOOL_META,
    ),
    "ssh_create_server": ToolSpec(
        name="ssh_create_server",
        title="Create SSH Server",
        description="Create a local YAML server configuration. Existing files are never overwritten.",
        args=(
            SERVER_ARG,
            _arg("config", "object", "Complete server configuration object", required=True),
        ),
        annotations={"readOnlyHint": False, "destructiveHint": False},
        handler=_handle_ssh_create_server,
    ),
    "ssh_update_server": ToolSpec(
        name="ssh_update_server",
        title="Update SSH Server",
        description="Recursively merge a local server configuration patch, or replace the full config.",
        args=(
            SERVER_ARG,
            _arg("config", "object", "Configuration patch or complete replacement", required=True),
            _arg("replace", "boolean", "Replace the full config instead of merging", default=False),
        ),
        annotations={"readOnlyHint": False, "destructiveHint": True},
        handler=_handle_ssh_update_server,
    ),
    "ssh_copy_server": ToolSpec(
        name="ssh_copy_server",
        title="Copy SSH Server",
        description="Copy a local server configuration to a new name. Existing files are never overwritten.",
        args=(
            _arg("source_server", "string", "Existing server name", required=True),
            _arg("target_server", "string", "New server name", required=True),
        ),
        annotations={"readOnlyHint": False, "destructiveHint": False},
        handler=_handle_ssh_copy_server,
    ),
    "ssh_delete_server": ToolSpec(
        name="ssh_delete_server",
        title="Delete SSH Server",
        description="Permanently delete one local YAML server configuration.",
        args=(SERVER_ARG,),
        annotations={"readOnlyHint": False, "destructiveHint": True},
        handler=_handle_ssh_delete_server,
    ),
    "ssh_run": ToolSpec(
        name="ssh_run",
        title="Run SSH Command",
        description="Execute a command on a remote server. Set sudo=true to run as root.",
        args=(
            SERVER_ARG,
            _arg("command", "string", "Command to execute", required=True),
            _arg("timeout", "number", "Timeout in seconds", default=60),
            SUDO_ARG,
        ),
        annotations={"readOnlyHint": False, "destructiveHint": True},
        handler=_handle_ssh_run,
    ),
    "ssh_upload_script": ToolSpec(
        name="ssh_upload_script",
        title="Upload SSH Script",
        description="Upload a local script to the server scripts_dir.",
        args=(
            SERVER_ARG,
            _arg("local_path", "string", "Local script path", required=True),
            _arg("script_name", "string", "Optional rename"),
            _arg("run_immediately", "boolean", "Run after upload", default=False),
            TIMEOUT_300_ARG,
            _arg("overwrite", "boolean", "Overwrite existing script", default=True),
            SUDO_ARG,
        ),
        annotations={"readOnlyHint": False, "destructiveHint": False},
        handler=_handle_ssh_upload_script,
    ),
    "ssh_run_script": ToolSpec(
        name="ssh_run_script",
        title="Run SSH Script",
        description="Execute an uploaded script from scripts_dir.",
        args=(
            SERVER_ARG,
            _arg("script_name", "string", "Script filename", required=True),
            TIMEOUT_300_ARG,
            _arg("sudo", "boolean", "Run as root", default=False),
        ),
        annotations={"readOnlyHint": False, "destructiveHint": False},
        handler=_handle_ssh_run_script,
    ),
    "ssh_list_scripts": ToolSpec(
        name="ssh_list_scripts",
        title="List SSH Scripts",
        description="List uploaded scripts on the remote server.",
        args=(SERVER_ARG, _arg("sudo", "boolean", "List as root", default=False)),
        annotations={"readOnlyHint": True},
        handler=_handle_ssh_list_scripts,
    ),
    "ssh_list_servers": ToolSpec(
        name="ssh_list_servers",
        title="List SSH Servers",
        description="List all available server configurations.",
        args=(),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "openWorldHint": False,
        },
        handler=_handle_ssh_list_servers,
        meta=APP_CALLABLE_TOOL_META,
        output_schema=SERVER_LIST_OUTPUT_SCHEMA,
    ),
    "ssh_download": ToolSpec(
        name="ssh_download",
        title="Download SSH File",
        description="Download a file or directory from the remote server to a local path.",
        args=(
            SERVER_ARG,
            _arg("remote_path", "string", "Remote file or directory path", required=True),
            _arg("local_path", "string", "Local file or directory path", required=True),
            _arg("pattern", "string", "Optional regex filename filter"),
            TIMEOUT_300_ARG,
            _arg("overwrite", "boolean", "Overwrite existing local files", default=True),
            _arg("sudo", "boolean", "Read root-owned files via sudo stage", default=False),
        ),
        annotations={"readOnlyHint": False, "destructiveHint": False},
        handler=_handle_ssh_download,
    ),
    "ssh_upload_all_scripts": ToolSpec(
        name="ssh_upload_all_scripts",
        title="Upload All SSH Scripts",
        description="Upload all scripts referenced by alias definitions.",
        args=(SERVER_ARG, _arg("sudo", "boolean", "Install as root", default=False)),
        annotations={"readOnlyHint": False, "destructiveHint": False},
        handler=_handle_ssh_upload_all_scripts,
    ),
    "ssh_run_alias": ToolSpec(
        name="ssh_run_alias",
        title="Run SSH Alias",
        description="Run an alias-defined quick command.",
        args=(
            SERVER_ARG,
            _arg("alias_name", "string", "Alias name", required=True),
        ),
        annotations={"readOnlyHint": False, "destructiveHint": True},
        handler=_handle_ssh_run_alias,
    ),
    "ssh_list_aliases": ToolSpec(
        name="ssh_list_aliases",
        title="List SSH Aliases",
        description="List quick commands configured for a server.",
        args=(SERVER_ARG,),
        annotations={"readOnlyHint": True},
        handler=_handle_ssh_list_aliases,
    ),
}


def _tool_specs() -> Dict[str, ToolSpec]:
    specs = dict(STATIC_TOOLS)
    specs.update(_alias_tool_specs())
    return specs


def handle_tools_call(params: dict) -> dict:
    params = _require_object(params, "params")
    name = params.get("name")
    if not isinstance(name, str) or not name:
        raise McpProtocolError(-32602, "Missing or invalid tool name")
    args = _require_object(params.get("arguments", {}), "arguments")

    spec = STATIC_TOOLS.get(name)
    if spec is None:
        spec = _alias_tool_specs().get(name)
    if spec is None:
        raise McpProtocolError(-32602, f"Unknown tool: {name}")
    return spec.handler(_validate_tool_args(args, spec.args))


def _build_tools_list() -> list:
    return [spec.tool() for spec in _tool_specs().values()]


def _build_resources_list() -> list:
    return [{
        "uri": CONFIG_UI_URI,
        "name": "ssh-server-config",
        "title": "SSH 服务器配置",
        "description": "添加、修改和删除服务器配置。",
        "mimeType": CONFIG_UI_MIME_TYPE,
    }]


def _read_resource(uri: str) -> dict:
    if uri != CONFIG_UI_URI:
        raise McpProtocolError(-32602, f"Unknown resource: {uri}")
    try:
        html = CONFIG_UI_PATH.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise McpProtocolError(-32603, "Configuration UI file is missing") from exc
    return {
        "contents": [{
            "uri": CONFIG_UI_URI,
            "mimeType": CONFIG_UI_MIME_TYPE,
            "text": html,
            "_meta": {
                "ui": {
                    "prefersBorder": False,
                    "csp": {"connectDomains": [], "resourceDomains": []},
                },
                "openai/widgetPrefersBorder": False,
                "openai/widgetDescription": "管理 SSH 服务器连接配置。",
            },
        }],
    }


def _negotiate_protocol_version(params: dict) -> str:
    requested = params.get("protocolVersion")
    if requested in SUPPORTED_PROTOCOL_VERSIONS:
        return requested
    return LATEST_PROTOCOL_VERSION


def handle_request(req: dict):
    if not isinstance(req, dict):
        raise McpProtocolError(-32600, "Invalid Request")
    if req.get("jsonrpc") != "2.0":
        raise McpProtocolError(-32600, "Invalid Request")
    if "method" not in req or not isinstance(req.get("method"), str):
        raise McpProtocolError(-32600, "Invalid Request")

    method = req["method"]
    is_notification = "id" not in req
    req_id = req.get("id")

    if is_notification:
        return None

    params = _require_object(req.get("params", {}), "params")

    if method == "ping":
        return jsonrpc_result(req_id, {})

    if method == "initialize":
        return jsonrpc_result(req_id, {
            "protocolVersion": _negotiate_protocol_version(params),
            "capabilities": {
                "tools": {"listChanged": False},
                "resources": {"subscribe": False, "listChanged": False},
            },
            "serverInfo": SERVER_INFO,
            "instructions": MCP_INSTRUCTIONS,
        })

    if method == "resources/list":
        cursor = params.get("cursor")
        if cursor not in (None, ""):
            raise McpProtocolError(-32602, "Invalid or expired pagination cursor")
        return jsonrpc_result(req_id, {"resources": _build_resources_list()})

    if method == "resources/read":
        uri = params.get("uri")
        if not isinstance(uri, str) or not uri:
            raise McpProtocolError(-32602, "Missing or invalid resource URI")
        return jsonrpc_result(req_id, _read_resource(uri))

    if method == "tools/list":
        cursor = params.get("cursor")
        if cursor not in (None, ""):
            raise McpProtocolError(-32602, "Invalid or expired pagination cursor")
        return jsonrpc_result(req_id, {"tools": _build_tools_list()})

    if method == "tools/call":
        try:
            return jsonrpc_result(req_id, handle_tools_call(params))
        except McpProtocolError:
            raise
        except Exception as e:
            name = params.get("name", "")
            args = _require_object(params.get("arguments", {}), "arguments")
            text = f"{_tool_args_summary(name, args)}\n\nError: {e}"
            return jsonrpc_result(req_id, mcp_text(text, True))

    raise McpProtocolError(-32601, f"Method not found: {method}")


def handle_message(msg):
    if isinstance(msg, list):
        if not msg:
            return jsonrpc_error(None, -32600, "Invalid Request")
        responses = []
        for item in msg:
            resp = _handle_message_item(item)
            if resp is not None:
                responses.append(resp)
        return responses or None
    return _handle_message_item(msg)


def _handle_message_item(item):
    try:
        return handle_request(item)
    except McpProtocolError as e:
        req_id = item.get("id") if isinstance(item, dict) and "id" in item else None
        return jsonrpc_error(req_id, e.code, e.message, e.data)
    except Exception as e:
        req_id = item.get("id") if isinstance(item, dict) and "id" in item else None
        return jsonrpc_error(req_id, -32603, str(e))


def write_response(msg):
    line = json.dumps(msg, ensure_ascii=False) + "\n"
    sys.stdout.write(line)
    sys.stdout.flush()


def main():
    """MCP stdio main loop."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            write_response(jsonrpc_error(None, -32700, "Parse error"))
            continue

        resp = handle_message(req)
        if resp is not None:
            write_response(resp)


if __name__ == "__main__":
    main()
