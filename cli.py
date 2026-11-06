#!/usr/bin/env python3
"""SSH Client CLI - Command line entry point

Usage:
    python cli.py list-servers
    python cli.py open-config
    python cli.py create-server <name> <config.yml>
    python cli.py update-server <name> <config.yml> [--replace]
    python cli.py copy-server <source> <new-name>
    python cli.py delete-server <name>
    python cli.py <server> run "<command>" [-s]      # -s = run as root via sudo
    python cli.py <server> run-script <name> [-s]    # run uploaded script (optional sudo)
    python cli.py <server> alias <name>              # sudo is part of the alias YAML config
    python cli.py <server> upload <local-script> [-r] [-s] [-n NAME]
    python cli.py <server> upload-all
    python cli.py <server> list-scripts
    python cli.py <server> list-aliases
    python cli.py <server> download <remote> <local> [-p PATTERN]

Examples:
    python cli.py my-server run "tail -100 /var/log/app.log"
    python cli.py my-server run "apt update" -s
    python cli.py my-server run-script restart.sh -s
    python cli.py my-server alias healthcheck
    python cli.py my-server upload-all
"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ssh_client import pool, _normalize_path, load_yaml

HELP = """SSH Client CLI

Usage:
    python cli.py list-servers
    python cli.py open-config
    python cli.py create-server <name> <config.yml>
    python cli.py update-server <name> <config.yml> [--replace]
    python cli.py copy-server <source> <new-name>
    python cli.py delete-server <name>
    python cli.py <server> <command> [args...] [-s] [-t SECONDS]

Commands:
    list-servers                List all configured servers
    open-config                 Open browser settings (keep this process running)
    create-server <name> <file> Create a server from a YAML config
    update-server <name> <file> Merge a YAML config patch (--replace for full replace)
    copy-server <source> <name> Copy a server config to a new name
    delete-server <name>        Delete a server config
    <server> run <cmd>          Run a command on remote server
    <server> run-script <name>  Run an uploaded script (from scripts_dir)
    <server> alias <name>       Run an alias-defined command (sudo set in YAML)
    <server> upload <path>      Upload local script (-r = run after upload; -n NAME)
    <server> download <remote> <local>  Download a file or dir from remote (-p PATTERN)
    <server> upload-all         Upload all scripts from alias definitions
    <server> list-scripts       List uploaded scripts on remote
    <server> list-aliases       List configured aliases (local, no SSH)

Common flags (work with ALL commands that connect to SSH):
    -s, --sudo            Execute the operation as root (requires sudo_password
                          in server config). For upload: stages via /tmp and
                          installs preserving original owner/mode (or matching
                          parent dir for new files). For download: stages via
                          /tmp + chown to user, then SFTPs (original file is
                          NEVER modified).
    -t, --timeout SECS    Command timeout in seconds (default: 300)

Per-command flags:
    -n, --name NAME       (upload) Custom script name on remote
    -r, --run             (upload) Run script immediately after upload
    -p, --pattern PAT     (download) Regex pattern to filter filenames

Examples:
    python cli.py my-server run "uptime"
    python cli.py my-server run "apt update" -s
    python cli.py my-server run-script restart.sh -s
    python cli.py my-server alias healthcheck
    python cli.py my-server upload ./fix.sh -r -s
    python cli.py my-server download /var/log/secure ./secure.log -s
    python cli.py my-server list-scripts -s     # list root-owned scripts dir
"""


def _extract_flag(args: list, *flags) -> bool:
    """Pop boolean flag from args, return True if present."""
    found = False
    for f in flags:
        while f in args:
            args.remove(f)
            found = True
    return found


def _extract_opt(args: list, *flags, default=None):
    """Pop --opt VALUE pair from args, return value or default."""
    for f in flags:
        if f in args:
            idx = args.index(f)
            if idx + 1 < len(args):
                value = args[idx + 1]
                del args[idx:idx + 2]
                return value
    return default


def _write_result(result: dict) -> int:
    """Write a standard SSH result and return its exit code."""
    if result.get("stdout"):
        sys.stdout.write(result["stdout"])
    if result.get("stderr"):
        sys.stderr.write(result["stderr"])
    if result.get("output_truncated"):
        sys.stderr.write("\nReturned output is incomplete (output limit or command timeout).\n")
    if result.get("timed_out"):
        sys.stderr.write("\nSSH command timed out.\n")
    return result.get("code", 1)


def _write_upload_result(result: dict) -> int:
    """Write upload output, including optional immediate-run streams."""
    if result.get("stdout"):
        sys.stdout.write(result["stdout"])
    if result.get("run_stdout"):
        sys.stdout.write(result["run_stdout"])
    if result.get("stderr"):
        sys.stderr.write(result["stderr"])
    if result.get("run_stderr"):
        sys.stderr.write(result["run_stderr"])
    return result.get("code", 1)


def main():
    if len(sys.argv) < 2 or sys.argv[1] == "-h" or sys.argv[1] == "--help":
        print(HELP)
        sys.exit(0)

    first = sys.argv[1]
    rest = sys.argv[2:]

    if first == "open-config":
        if rest:
            print("Usage: python cli.py open-config", file=sys.stderr)
            sys.exit(1)
        import mcp_server
        result = mcp_server._handle_ssh_open_config({})
        print(json.dumps(result["structuredContent"], indent=2, ensure_ascii=False), flush=True)
        try:
            mcp_server._config_web_server.wait()
        except KeyboardInterrupt:
            pass
        finally:
            mcp_server._config_web_server.close()
        return

    # list-servers
    if first == "list-servers":
        servers = pool.list_servers()
        print(json.dumps({"count": len(servers), "servers": servers}, indent=2, ensure_ascii=False))
        return

    if first in ("create-server", "update-server"):
        if len(rest) < 2:
            print(f"Usage: python cli.py {first} <name> <config.yml>", file=sys.stderr)
            sys.exit(1)
        name = rest[0]
        config = load_yaml(_normalize_path(rest[1]))
        if first == "create-server":
            result = pool.create_server(name, config)
        else:
            result = pool.update_server(
                name, config, replace=_extract_flag(rest, "--replace")
            )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    if first == "delete-server":
        if len(rest) != 1:
            print("Usage: python cli.py delete-server <name>", file=sys.stderr)
            sys.exit(1)
        print(json.dumps(pool.delete_server(rest[0]), indent=2, ensure_ascii=False))
        return

    if first == "copy-server":
        if len(rest) != 2:
            print("Usage: python cli.py copy-server <source> <new-name>", file=sys.stderr)
            sys.exit(1)
        result = pool.copy_server(rest[0], rest[1])
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    # Get connection
    conn = pool.get(first)

    # Parse common flags first (mutating `rest`)
    timeout_str = _extract_opt(rest, "-t", "--timeout")
    timeout = int(timeout_str) if timeout_str is not None else conn.timeout
    sudo = _extract_flag(rest, "-s", "--sudo")

    # Route command
    cmd = rest[0] if rest else None

    if cmd == "run" and len(rest) >= 2:
        command = " ".join(rest[1:])
        result = conn.run(
            command, timeout=timeout, sudo=sudo,
            stream_cb=lambda chunk, is_stderr: (
                sys.stderr.write(chunk) if is_stderr else sys.stdout.write(chunk)
            ),
        )
        sys.exit(result.get("code", 1))

    elif cmd == "alias" and len(rest) >= 2:
        # sudo for alias is defined inside the YAML (per-alias `sudo: true`)
        result = conn.run_alias(rest[1], stream_cb=lambda chunk, is_stderr: (
            sys.stderr.write(chunk) if is_stderr else sys.stdout.write(chunk)
        ))
        sys.exit(result.get("code", 1))

    elif cmd == "run-script" and len(rest) >= 2:
        result = conn.run_script(rest[1], timeout=timeout, sudo=sudo, stream_cb=lambda chunk, is_stderr: (
            sys.stderr.write(chunk) if is_stderr else sys.stdout.write(chunk)
        ))
        sys.exit(result.get("code", 1))

    elif cmd == "upload" and len(rest) >= 2:
        local_script = _normalize_path(rest[1])
        run_immediately = _extract_flag(rest, "-r", "--run")
        name = _extract_opt(rest, "-n", "--name")
        result = conn.upload_script(local_script, script_name=name,
                                     run_immediately=run_immediately,
                                     timeout=timeout, sudo=sudo)
        sys.exit(_write_upload_result(result))

    elif cmd == "upload-all":
        result = conn.upload_all_scripts(sudo=sudo)
        sys.exit(_write_result(result))

    elif cmd == "list-scripts":
        result = conn.list_scripts(sudo=sudo)
        sys.exit(_write_result(result))

    elif cmd == "list-aliases":
        aliases = conn.list_aliases()
        print(json.dumps({"count": len(aliases), "aliases": aliases}, indent=2, ensure_ascii=False))

    elif cmd == "download" and len(rest) >= 3:
        remote_path = rest[1]
        local_path = _normalize_path(rest[2])
        pattern = _extract_opt(rest, "-p", "--pattern")
        overwrite = not _extract_flag(rest, "--no-overwrite")
        result = conn.download(remote_path, local_path, timeout=timeout,
                               pattern=pattern, overwrite=overwrite, sudo=sudo)
        sys.exit(_write_result(result))

    else:
        print(f"Usage: python cli.py <server> <command>", file=sys.stderr)
        print(HELP, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    finally:
        pool.close_all()
