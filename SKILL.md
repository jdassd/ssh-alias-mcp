---
name: ssh-alias-mcp
description: >
  SSH server management CLI. Run remote commands, deploy scripts, restart services,
  check logs, upload/download files, and manage aliases across Linux (bash) and Windows
  (cmd/powershell) servers. Use when managing servers, deploying code, checking logs,
  restarting services, uploading scripts, or running SSH commands. Trigger keywords:
  deploy, restart, check logs, server health, SSH, run on server, upload script,
  download file, server alias, 部署, 重启, 查看日志, 服务器健康检查.
---

# ssh-alias-mcp CLI

## Quick Start

```bash
# Run from the installed plugin directory with its Python dependencies available.
python cli.py <server> <subcommand> [args...] [-s] [-t SECS]
```

## Commands

```bash
python cli.py list-servers                                    # List all servers
python cli.py create-server <name> <config.yml>               # Create server config
python cli.py update-server <name> <patch.yml> [--replace]    # Update/replace config
python cli.py copy-server <source> <new-name>                 # Copy server config
python cli.py delete-server <name>                            # Delete server config
python cli.py <server> run "<cmd>" [-s] [-t sec]             # Run command (real-time stream)
python cli.py <server> run-script <name> [-s] [-t sec]       # Run uploaded script
python cli.py <server> alias <name>                           # Run alias (sudo from YAML)
python cli.py <server> upload <path> [-r] [-s] [-n NAME] [-t sec]  # Upload script
python cli.py <server> upload-all [-s] [-t sec]              # Upload all alias scripts
python cli.py <server> download <remote> <local> [-s] [-p REGEX] [-t sec]  # Download file/dir
python cli.py <server> list-scripts [-s] [-t sec]            # List remote scripts
python cli.py <server> list-aliases                          # List aliases (no SSH)
```

## Flags

| Flag | Description |
|------|-------------|
| `-s` / `--sudo` | Run as root (upload: stages via /tmp preserving perms; download: /tmp + chown) |
| `-t` / `--timeout` | Timeout in seconds (default 300) |
| `-r` / `--run` | (upload) Execute immediately after upload |
| `-n` / `--name` | (upload) Custom remote filename |
| `-p` / `--pattern` | (download) Regex filter for filenames |

## Notes

- **sudo** — Use `-s` flag, never inline `sudo -S` in commands
- **Real-time stream** — `run`/`run-script`/`alias` stream via `stream_cb`, not buffered
- **Docker build** — Add `--progress=plain` for real-time output. Never `| tail` (blocks until done)
- **Shell** — Set `shell` in server YAML: `bash` / `cmd` / `powershell`
- **Jump host** — Set `server.jump_host` to another server config name; the CLI and MCP tools use it automatically
- **Configuration UI** — Use `ssh_open_config` to open server settings in the default browser. If `browser_opened` is false, open its returned `url`. The CLI equivalent is `python cli.py open-config`; keep its process running while editing.

## More

- Server config, aliases, security, file transfer → [REFERENCE.md](REFERENCE.md)
- Full CLI reference → [CLI_USAGE.md](CLI_USAGE.md)
- AI Agent installation guide → [INSTALL.md](INSTALL.md)
