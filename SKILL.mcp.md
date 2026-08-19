---
name: ssh-alias-mcp
description: >
  AI-driven server operations via SSH. Run commands, deploy scripts, restart services,
  check logs, upload/download files, and manage aliases across Linux and Windows servers.
  Supports bash, cmd, and powershell shells. Use when managing servers, deploying code,
  checking logs, restarting services, uploading scripts, or running SSH commands.
  Trigger keywords: deploy, restart, check logs, server health, SSH, run on server,
  upload script, download file, server alias, 部署, 重启, 查看日志, 服务器健康检查.
---

# ssh-alias-mcp

## Quick Start

```bash
# Install (one-time)
pip install -r requirements.txt

# CLI usage
python cli.py list-servers
python cli.py <server> run "uptime"
python cli.py <server> alias deploy
```

## MCP Tools

| Tool | Parameters | Description |
|------|------------|-------------|
| `ssh_open_config` | — | Open the server configuration UI |
| `ssh_list_servers` | — | List all configured servers |
| `ssh_create_server` | `server`, `config` | Create a server config; refuses overwrite |
| `ssh_update_server` | `server`, `config`, `replace`(default false) | Merge a config patch or replace it |
| `ssh_copy_server` | `source_server`, `target_server` | Copy a server config to a new name |
| `ssh_delete_server` | `server` | Delete a server config |
| `ssh_list_aliases` | `server` | List quick-command aliases |
| `ssh_list_scripts` | `server`, `sudo`(default false) | List uploaded scripts on remote |
| `ssh_run` | `server`, `command`, `timeout`(default 60s), `sudo`(default false) | Execute a command |
| `ssh_run_alias` | `server`, `alias_name` | Run an alias |
| `ssh_run_script` | `server`, `script_name`, `timeout`(default 300s), `sudo`(default false) | Run an uploaded script |
| `ssh_upload_script` | `server`, `local_path`, `script_name`(opt), `run_immediately`(default false), `timeout`(default 300s), `overwrite`(default true), `sudo`(default false) | Upload a script |
| `ssh_download` | `server`, `remote_path`, `local_path`, `pattern`(opt), `timeout`(default 300s), `sudo`(default false) | Download file or directory |
| `ssh_upload_all_scripts` | `server`, `sudo`(default false) | Upload all scripts from alias definitions |
| `ssh_alias.{server}.{name}` | — (auto-generated) | One-click alias execution |

### Notes
- **sudo** — Use `ssh_run` with `sudo: true`, never inline `sudo -S` in commands
- **Dynamic alias tools** — MCP exposes aliases as `ssh_alias.{server}.{name}`
- **Live stream output** — For real-time streaming, use CLI. See [CLI_USAGE.md](CLI_USAGE.md)
- **Jump host** — Set `server.jump_host` to another server config name; all MCP and CLI operations route through it automatically
- **Configuration UI** — Use `ssh_open_config` to manage multiple server configs in the conversation
- **Docker build real-time output** — `docker build` uses buildkit JSON output by default. Add `--progress=plain` for real-time output. NEVER pipe to `tail` (e.g. `| tail -5`) as it buffers until command completes. Example: `docker build --progress=plain -t myimage .`

## Shell Support

Set `shell` in server YAML to auto-adapt commands:

| Shell | Use Case |
|-------|---------|
| `bash` | Linux/macOS servers (default) |
| `cmd` | Windows servers (CMD) |
| `powershell` | Windows servers (PowerShell) |

## More

- Server config, aliases, security, file transfer → [REFERENCE.md](REFERENCE.md)
- CLI commands reference → [CLI_USAGE.md](CLI_USAGE.md)
- AI Agent installation guide → [INSTALL.md](INSTALL.md)
