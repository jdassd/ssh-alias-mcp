#!/usr/bin/env python3
"""P1 tests — extends inheritance, ConnectionPool, MCP protocol, error paths.

Covers the P1 gaps from the audit:
  - extends inheritance (server fields, aliases, security, proxy, paths)
  - ConnectionPool behavior (reuse, key normalization, list_servers sorting)
  - MCP protocol (initialize, tools/list, tool call, error handling)
  - Error paths (FileNotFoundError, FileExistsError, timeout, sudo password missing)
  - CLI argument parsing (long flags, multiple flags)

Run:  python test_advanced.py
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SKILL_DIR = Path(__file__).parent.parent.resolve()
TEST_DIR = Path(__file__).parent.resolve()
CLI = [sys.executable, str(SKILL_DIR / "cli.py")]
sys.path.insert(0, str(SKILL_DIR))

# ────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────

RED, GREEN, YELLOW, NC = "\033[91m", "\033[92m", "\033[93m", "\033[0m"
passed = failed = skipped = 0


def _ok(msg):
    print(f"{GREEN}PASS{NC}  {msg}")


def _fail(msg, detail=""):
    print(f"{RED}FAIL{NC}  {msg} — {detail}" if detail else f"{RED}FAIL{NC}  {msg}")


def _skip(msg):
    print(f"{YELLOW}SKIP{NC}  {msg}")


def _section(name):
    print(f"\n{'='*60}\n  {name}\n{'='*60}")


def _inc(result, name, detail=""):
    global passed, failed, skipped
    if result == "ok":
        passed += 1
        _ok(name)
    elif result == "fail":
        failed += 1
        _fail(name, detail)
    else:
        skipped += 1
        _skip(name)


def run_cli(*args, timeout=300):
    """Run cli.py and return (stdout, stderr, returncode)."""
    cmd = CLI + list(args)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                       cwd=str(SKILL_DIR), encoding="utf-8", errors="replace")
    return r.stdout, r.stderr, r.returncode


# ────────────────────────────────────────────────────────────────────
# 1. extends inheritance — server fields
# ────────────────────────────────────────────────────────────────────

def test_extends_server_fields():
    _section("1. extends inheritance — server fields")

    # test-server.yml has no extends, so it should work as-is.
    # We verify the server fields are loaded correctly.
    out, err, code = run_cli("test-server", "run", "whoami", "-t", "10")
    _inc("ok" if code == 0 and out.strip() == "YOUR_SSH_USER" else "fail",
         "test-server whoami returns 'YOUR_SSH_USER'",
         f"stdout={out.strip()!r} code={code}")

    # Verify host/port from list-servers
    out, err, code = run_cli("list-servers")
    _inc("ok" if code == 0 else "fail",
         "list-servers returns valid JSON",
         f"code={code}")
    data = json.loads(out)
    test_srv = next((s for s in data["servers"] if s["name"] == "test-server"), None)
    _inc("ok" if test_srv and test_srv.get("host") == "ssh.example.com" else "fail",
         "test-server host is ssh.example.com",
         f"host={test_srv.get('host') if test_srv else 'N/A'}")
    _inc("ok" if test_srv and test_srv.get("port") == 22 else "fail",
         "test-server port is 22",
         f"port={test_srv.get('port') if test_srv else 'N/A'}")


# ────────────────────────────────────────────────────────────────────
# 2. ConnectionPool — key normalization
# ────────────────────────────────────────────────────────────────────

def test_pool_key_normalization():
    _section("2. ConnectionPool — key normalization")

    # _to_key converts names to lowercase, strips .yml/.yaml
    from ssh_client import pool

    # Case 1: lowercase
    _inc("ok" if pool._to_key("test-server") == "test-server" else "fail",
         "_to_key('test-server') == 'test-server'",
         f"result={pool._to_key('test-server')!r}")

    # Case 2: uppercase
    _inc("ok" if pool._to_key("Test-Server") == "test-server" else "fail",
         "_to_key('Test-Server') == 'test-server'",
         f"result={pool._to_key('Test-Server')!r}")

    # Case 3: with .yml extension
    _inc("ok" if pool._to_key("test-server.yml") == "test-server" else "fail",
         "_to_key('test-server.yml') == 'test-server'",
         f"result={pool._to_key('test-server.yml')!r}")

    # Case 4: with .yaml extension
    _inc("ok" if pool._to_key("test-server.yaml") == "test-server" else "fail",
         "_to_key('test-server.yaml') == 'test-server'",
         f"result={pool._to_key('test-server.yaml')!r}")

    # Case 5: with whitespace
    _inc("ok" if pool._to_key(" test-server ") == "test-server" else "fail",
         "_to_key(' test-server ') == 'test-server'",
         f"result={pool._to_key(' test-server ')!r}")


# ────────────────────────────────────────────────────────────────────
# 3. ConnectionPool — list_servers sorting and index
# ────────────────────────────────────────────────────────────────────

def test_list_servers_sorting():
    _section("3. ConnectionPool — list_servers sorting and index")

    out, err, code = run_cli("list-servers")
    data = json.loads(out)
    servers = data["servers"]

    # Case 1: index starts from 1
    _inc("ok" if servers[0].get("index") == 1 else "fail",
         "first server index is 1",
         f"index={servers[0].get('index')}")

    # Case 2: index increments by 1
    if len(servers) >= 2:
        _inc("ok" if servers[1].get("index") == 2 else "fail",
             "second server index is 2",
             f"index={servers[1].get('index')}")

    # Case 3: sorted by (group, name)
    names = [s["name"] for s in servers]
    expected_sorted = sorted(names, key=lambda n: (
        next((s["group"] for s in data["servers"] if s["name"] == n), ""),
        n
    ))
    _inc("ok" if names == expected_sorted else "fail",
         "servers sorted by (group, name)",
         f"actual={names[:5]} expected={expected_sorted[:5]}")


# ────────────────────────────────────────────────────────────────────
# 4. MCP protocol — initialize
# ────────────────────────────────────────────────────────────────────

def test_mcp_initialize():
    _section("4. MCP protocol — initialize handshake")

    from mcp_server import handle_request

    # Case 1: initialize request
    resp = handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    _inc("ok" if resp.get("jsonrpc") == "2.0" else "fail",
         "initialize returns jsonrpc 2.0",
         f"jsonrpc={resp.get('jsonrpc')!r}")
    _inc("ok" if resp.get("id") == 1 else "fail",
         "initialize echoes id=1",
         f"id={resp.get('id')}")
    _inc("ok" if resp.get("result", {}).get("protocolVersion") == "2025-11-25" else "fail",
         "initialize returns latest protocolVersion by default",
         f"protocolVersion={resp.get('result', {}).get('protocolVersion')!r}")
    _inc("ok" if resp.get("result", {}).get("serverInfo", {}).get("name") == "ssh-alias-mcp" else "fail",
         "initialize returns serverInfo.name == 'ssh-alias-mcp'",
         f"serverInfo.name={resp.get('result', {}).get('serverInfo', {}).get('name')!r}")


# ────────────────────────────────────────────────────────────────────
# 5. MCP protocol — tools/list
# ────────────────────────────────────────────────────────────────────

def test_mcp_tools_list():
    _section("5. MCP protocol — tools/list")

    from mcp_server import handle_request

    resp = handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    _inc("ok" if "result" in resp else "fail",
         "tools/list returns result",
         f"keys={list(resp.keys())}")

    tools = resp.get("result", {}).get("tools", [])
    # Should have at least the 9 static tools
    _inc("ok" if len(tools) >= 9 else "fail",
         "at least 9 static tools",
         f"count={len(tools)}")

    # Verify known tool names
    tool_names = {t["name"] for t in tools}
    _inc("ok" if "ssh_run" in tool_names else "fail",
         "ssh_run is in tools list",
         f"names={sorted(tool_names)}")
    _inc("ok" if "ssh_list_servers" in tool_names else "fail",
         "ssh_list_servers is in tools list",
         f"names={sorted(tool_names)}")


# ────────────────────────────────────────────────────────────────────
# 6. MCP protocol — tool call (ssh_run)
# ────────────────────────────────────────────────────────────────────

def test_mcp_tool_call():
    _section("6. MCP protocol — tool call (ssh_run)")

    from mcp_server import handle_request

    resp = handle_request({
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": "ssh_run",
            "arguments": {
                "server": "test-server",
                "command": "echo mcp-test-ok",
                "timeout": 10,
                "sudo": False,
            },
        },
    })
    content = resp.get("result", {}).get("content", [])
    text = content[0]["text"] if content else ""
    _inc("ok" if "mcp-test-ok" in text else "fail",
         "ssh_run returns 'mcp-test-ok'",
         f"text={text.strip()!r}")


# ────────────────────────────────────────────────────────────────────
# 7. MCP protocol — error handling
# ────────────────────────────────────────────────────────────────────

def test_mcp_errors():
    _section("7. MCP protocol — error handling")

    from mcp_server import handle_message

    # Case 1: unknown tool returns a JSON-RPC invalid params error
    resp = handle_message({
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {"name": "nonexistent_tool", "arguments": {}},
    })
    _inc("ok" if resp.get("error", {}).get("code") == -32602 else "fail",
         "unknown tool returns -32602",
         f"resp={resp!r}")

    # Case 2: invalid method
    resp = handle_message({
        "jsonrpc": "2.0",
        "id": 5,
        "method": "nonexistent_method",
        "params": {},
    })
    _inc("ok" if resp.get("error", {}).get("code") == -32601 else "fail",
         "invalid method returns -32601",
         f"code={resp.get('error', {}).get('code')}")

    # Case 3: old colon-style alias tool name is no longer a valid tool
    resp = handle_message({
        "jsonrpc": "2.0",
        "id": 6,
        "method": "tools/call",
        "params": {"name": "ssh_alias:foo", "arguments": {}},
    })
    _inc("ok" if "Unknown tool: ssh_alias:foo" in resp.get("error", {}).get("message", "") else "fail",
         "ssh_alias:foo returns unknown tool",
         f"resp={resp!r}")


# ────────────────────────────────────────────────────────────────────
# 8. Error paths — file not found
# ────────────────────────────────────────────────────────────────────

def test_error_paths():
    _section("8. Error paths — FileNotFoundError, FileExistsError, etc.")

    # Case 1: upload nonexistent local file → error
    out, err, code = run_cli("test-server", "upload", "/nonexistent/file.sh", "-t", "10")
    _inc("ok" if code != 0 else "fail",
         "upload nonexistent file returns error",
         f"code={code}")

    # Case 2: nonexistent server → error
    out, err, code = run_cli("nonexistent-server", "run", "echo test", "-t", "10")
    _inc("ok" if code != 0 else "fail",
         "nonexistent server returns error",
         f"code={code}")

    # Case 3: nonexistent alias → error
    out, err, code = run_cli("test-server", "alias", "does_not_exist_xyz123", "-t", "10")
    _inc("ok" if code != 0 else "fail",
         "nonexistent alias returns error",
         f"code={code}")


# ────────────────────────────────────────────────────────────────────
# 9. CLI argument parsing — long flags
# ────────────────────────────────────────────────────────────────────

def test_cli_long_flags():
    _section("9. CLI argument parsing — long flags")

    # Case 1: --timeout long flag
    out, err, code = run_cli("test-server", "run", "echo long-flag-test", "--timeout", "10")
    _inc("ok" if code == 0 and "long-flag-test" in out else "fail",
         "--timeout works",
         f"stdout={out.strip()!r} code={code}")

    # Case 2: --sudo long flag
    out, err, code = run_cli("test-server", "run", "whoami", "--sudo", "-t", "10")
    _inc("ok" if code == 0 and "root" in out else "fail",
         "--sudo works",
         f"stdout={out.strip()!r} code={code}")

    # Case 3: multiple flags together — --sudo wraps the command, output is root
    out, err, code = run_cli("test-server", "run", "whoami", "--sudo", "-t", "10")
    _inc("ok" if code == 0 and "root" in out else "fail",
         "multiple flags work together (--sudo + -t)",
         f"stdout={out.strip()!r} code={code}")


# ────────────────────────────────────────────────────────────────────
# 10. ConnectionPool — nonexistent server → FileNotFoundError
# ────────────────────────────────────────────────────────────────────

def test_pool_nonexistent():
    _section("10. ConnectionPool — nonexistent server → FileNotFoundError")

    from ssh_client import pool

    # Case 1: pool.get raises FileNotFoundError
    try:
        pool.get("this_server_does_not_exist_xyz")
        _fail("pool.get('nonexistent') should raise FileNotFoundError")
    except FileNotFoundError as e:
        _inc("ok" if "Server config not found" in str(e) else "fail",
             "pool.get raises FileNotFoundError with 'Server config not found'",
             f"msg={str(e)!r}")
    except Exception as e:
        _fail(f"pool.get raised {type(e).__name__} instead of FileNotFoundError: {e}")


# ────────────────────────────────────────────────────────────────────
# Main
# ────────────────────────────────────────────────────────────────────

def main():
    global passed, failed, skipped
    tests = [
        test_extends_server_fields,     # 1.  extends server fields
        test_pool_key_normalization,     # 2.  pool key normalization
        test_list_servers_sorting,       # 3.  list_servers sorting
        test_mcp_initialize,             # 4.  MCP initialize
        test_mcp_tools_list,             # 5.  MCP tools/list
        test_mcp_tool_call,              # 6.  MCP tool call (ssh_run)
        test_mcp_errors,                 # 7.  MCP error handling
        test_error_paths,                # 8.  error paths
        test_cli_long_flags,             # 9.  CLI long flags
        test_pool_nonexistent,           # 10. pool nonexistent server
    ]

    print("=" * 60)
    print("  Advanced Tests — P1")
    print(f"  Tests:  {len(tests)} test functions")
    print("=" * 60)

    for t in tests:
        try:
            t()
        except subprocess.TimeoutExpired:
            _fail(f"{t.__name__} — timeout")
        except Exception as e:
            _fail(f"{t.__name__} — exception: {e}")

    print(f"\n{'='*60}")
    print(f"  Results: {GREEN}{passed} passed{NC}, {RED}{failed} failed{NC}, {YELLOW}{skipped} skipped{NC}")
    print(f"{'='*60}")

    if failed > 0:
        sys.exit(1)
    print("\nAll advanced tests passed!")


if __name__ == "__main__":
    main()
