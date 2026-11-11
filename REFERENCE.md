# REFERENCE — Server Configuration, Aliases, Security & File Transfer

## Architecture

```
┌──────────────────────────────────────────────────────┐
│  AI Agent (Claude Code / Codex / OpenCode / ...)      │
│  └── MCP tool calls (ssh_run, ssh_alias.*)         │
└──────────────┬───────────────────────────────────────┘
               │ JSON-RPC over stdio
┌──────────────▼───────────────────────────────────────┐
│  mcp_server.py  (MCP stdio server)                   │
│  └── Dynamically exposes aliases as MCP tools        │
└──────────────┬───────────────────────────────────────┘
               │
┌──────────────▼───────────────────────────────────────┐
│  ssh_client.py  (Shared core)                        │
│  ├── ConnectionPool (connection reuse, 60s keepalive)│
│  ├── SSH jump host forwarding (direct-tcpip)         │
│  ├── HTTP CONNECT / SOCKS5 proxy, no direct fallback             │
│  └── SFTP script upload & download                   │
└──────────────┬───────────────────────────────────────┘
               │ SSH
┌──────────────▼───────────────────────────────────────┐
│  Remote Server (Linux / Windows)                     │
└──────────────────────────────────────────────────────┘
```

## Directory Structure

```
├── <skills-dir>/
│   └── ssh-alias-mcp/         # Core code
│       ├── SKILL.md           # AI Agent skill definition
│       ├── REFERENCE.md       # Detailed reference (this file)
│       ├── CLI_USAGE.md       # CLI commands reference
│       ├── INSTALL.md         # AI Agent installation guide
│       ├── cli.py             # CLI entry point
│       ├── mcp_server.py      # MCP stdio server
│       ├── ssh_client.py      # Core module (SSH connection, proxy, pool)
│       ├── config_web.py      # Loopback-only configuration server
│       ├── ui/config.html     # Server configuration form
│       ├── ui/browser.js      # Authenticated browser transport
│       ├── config.yaml        # Global config (servers_dir, proxy, timeout)
│       └── requirements.txt   # Python dependencies
│
└── servers/                   # Server config directory (path in config.yaml)
    ├── my-server.yml          # Server connection info + alias definitions
    ├── _shared/common.yml     # Shared aliases (inherited via extends)
    └── my-server/             # Local shell scripts for this server (.sh)
```

> Server configs `servers/` are separated from the skill directory for easy management and credential isolation. Path configured via `servers_dir` in `config.yaml` (absolute or relative path supported).

## Global Configuration (`config.yaml`)

```yaml
# servers/ directory path (absolute or relative to this file)
servers_dir: "../servers"

proxy:
  enabled: true               # Explicit false selects direct TCP
  type: http                  # HTTP CONNECT; legacy socks5 is also supported
  # host/port omitted: Windows 127.0.0.1:18080; Linux 127.0.0.1:18081
  # auth_file: ~/.config/openvpn-http-proxy/proxy-auth.json
  timeout: 30                 # Proxy connection timeout (seconds)

server:
  timeout: 30                 # Default SSH connection timeout (seconds)
  auto_reconnect: true        # Automatically reconnect on lost connection
  reconnect_interval: 5       # Interval between reconnection attempts (seconds)

security:
  blacklist: []               # Regex patterns to block commands
  whitelist: []               # Regex patterns to allow commands (empty = no restriction)
  command_template: ""        # Command template to wrap all commands
```

HTTP CONNECT carries the SSH TCP connection, including commands, aliases, SFTP uploads/downloads, and the first hop of a jump-host chain. Proxy failures are reported and never fall back to direct TCP. Configure a target with `server.proxy: {enabled: false}` for an intentional direct connection.

Missing or null `server.proxy` inherits the global setting. If neither setting exists, the authenticated HTTP proxy above is the default; old data does not need per-server migration. Existing explicit `enabled: false` stays direct. Existing endpoint-only proxy objects without `type` retain their SOCKS5 meaning. A target using `jump_host` inherits the jump host's transport; configure any proxy on that jump host.

HTTP settings support `host`, `port`, `username`, `password`, `auth_file`, and `timeout`. Credentials are read at connection time from the JSON `auth_file` containing `username` and `password`; the default is `~/.config/openvpn-http-proxy/proxy-auth.json`. Explicit username/password take priority. Set `auth_file: null` for an unauthenticated custom proxy. `VPN_HTTP_PROXY_URL=http://host:port` and `VPN_HTTP_PROXY_AUTH_FILE=/path/to/proxy-auth.json` override shared defaults; per-server/global explicit fields still take precedence. Passwords are not returned by the configuration UI or written to proxy error logs.

`auto_reconnect` enables background reconnects after a failed request.
`reconnect_interval` is used while there have been fewer than five consecutive
connection failures. After the fifth failure, retries slow to once every 60
seconds. A new CLI or MCP request always triggers an immediate attempt, even
during that cooldown. Set `auto_reconnect: false` to disable background attempts.

## Server Configuration (`{servers_dir}/{name}.yml`)

Create a `.yml` file for each server in the `servers/` directory:

```yaml
# my-server.yml

server:
  # === Required fields ===
  host: "your.host.com"         # Server IP or hostname
  user: "username"               # SSH login username

  # === Display info ===
  name: "My Server"              # Display name
  desc: "Application server"    # Description
  group: "dept/team"            # Group identifier (multi-level path for sorting)

  # Authentication (choose one)
  password: "your-password"      # Password auth
  # key: "/path/to/private_key"     # Or key auth
  # key_password: "passphrase"      # Key passphrase (optional)
  sudo_password: "sudo-pass"     # Sudo password (optional, defaults to password)

  # === Optional fields ===
  port: 22                       # SSH port (default 22)
  timeout: 30                    # SSH connection timeout (seconds, default 30)
  scripts_dir: "/home/user/scripts"  # Remote script directory
  shell: "bash"                  # Shell type (bash / cmd / powershell, default bash)
  system: "Ubuntu 24.04 LTS"    # OS information

  # SSH jump host (optional; references another server config by name)
  # jump_host: "bastion-prod"

  # Server-level proxy override (optional, overrides global config.yaml)
  proxy:
    host: "127.0.0.1"
    port: 10808

  # Security: command filtering (overrides global config.yaml)
  blacklist:
    - "rm -rf|mkfs|dd "          # Regex pattern, match = block
  whitelist:
    - "ls|df|docker|tail"        # Regex pattern, only allow matching commands
  command_template: "cd /opt/app && <command>"  # Auto-wrap commands

  # Upload/download path restrictions (empty = no restriction)
  allowed_local_paths:
    - "/home/bit/scripts"
  allowed_remote_paths:
    - "/home/bit/scripts"

# Quick commands — automatically exposed as MCP tools
aliases:
  # Script type: upload local script file then execute
  - name: deploy
    script: app-deploy.sh        # Script path, relative to YAML file directory
    desc: "Deploy application"
    timeout: 600                 # Command timeout (seconds), default 300
    sudo: true                   # Optional, execute with sudo

  # Inline type: execute a command directly on remote
  - name: logs
    inline: "docker logs --tail 100 my-app"
    desc: "View logs"
    timeout: 10
```

## SSH Jump Host (Bastion)

Define the bastion and target as separate server configs. The target's
`jump_host` value is the bastion file name without `.yml`:

```yaml
# servers/bastion-prod.yml
server:
  host: "203.0.113.10"
  user: "ops"
  key: "/home/me/.ssh/bastion_ed25519"
```

```yaml
# servers/internal-app.yml
server:
  host: "10.0.0.20"
  user: "deploy"
  password: "target-password"
  jump_host: "bastion-prod"
```

All CLI and MCP operations on `internal-app` first connect to `bastion-prod`,
open an SSH `direct-tcpip` channel to `10.0.0.20:22`, and then establish the
target SSH session over that channel. Bastion and target authentication remain
separate; the target credentials are used only by the target SSH handshake.

- One jump-host layer is supported. Self references, cycles, and a bastion that
  also configures `jump_host` are rejected.
- A target with `jump_host` cannot also set `server.proxy`. To use
  SOCKS5 → bastion → target, configure the proxy on the bastion instead.
- A failed jump-host connection never falls back to a direct target connection.
- The bastion SSH server must permit TCP forwarding (for example,
  `AllowTcpForwarding yes` in OpenSSH server configuration).

## Shared Alias Inheritance (`extends`)

### Three-Level Inheritance Chain

```
config.yaml (global defaults: proxy, timeout)
    ↓ extends
_shared/*.yml (shared connection info, shared aliases)
    ↓ extends
Server YAML (your server — override + append)
```

### Multiple Inheritance

A server YAML can inherit from multiple files — layered configuration composition:

```yaml
# my-server.yml
extends:
  - _shared/conn-base.yml    # ① Shared host, user, scripts_dir
  - _shared/common.yml       # ② Shared aliases

server:
  host: "198.51.100.10"      # Override ①'s host
  port: 22
  password: "xxx"

aliases:                      # Append — local aliases overlay on inherited
  - name: deploy
    script: deploy.sh
```

### Merge Rules

| Field | Merge Behavior |
|-------|----------------|
| `server` | Shallow merge — local overrides base |
| `aliases` | All inherited from base. Same `name` overrides, different names append |
| `security` (whitelist/blacklist/command_template) | Base sets defaults, local inherits. Local overrides if present |
| `proxy` | Same as security — local falls back to base |
| `allowed_local_paths` / `allowed_remote_paths` | Same as security — local falls back to base |

## Shell Type Support

### Three Shell Types

| Shell | Use Case | Command Template |
|-------|---------|-----------------|
| `bash` | Linux/macOS servers | Full bash command set |
| `cmd` | Windows servers (CMD) | `cmd /c "..."` wrapper |
| `powershell` | Windows servers (PowerShell) | Native PowerShell syntax |

### Shell Command Template Comparison

| Template | bash | cmd | powershell |
|----------|------|-----|------------|
| `mkdir` | `mkdir -p {path}` | `cmd /c "mkdir \\"{path}\\" 2>nul"` | `New-Item -ItemType Directory -Path "{path}" -Force` |
| `file_exists` | `test -f {path}` | `cmd /c "if exist \\"{path}\\" echo exists"` | `Test-Path "{path}"` |
| `run_script` | `bash {path}` | `cmd /c "call {path}"` | `& "{path}"` |
| `chmod` | `chmod {mode:o} {path}` | Not supported | Not supported |
| `stat_owner` | `stat -c '%U:%G' {path}` | Not supported | Not supported |
| `stat_mode` | `stat -c '%a' {path}` | Not supported | Not supported |
| `rm_dir` | `rm -rf {path}` | `cmd /c "rmdir /S /Q \\"{path}\\""` | `Remove-Item -Recurse -Force "{path}"` |
| `cp_r` | `cp -r {src} {dst}` | `cmd /c "xcopy /Y \\"{src}\\" \\"{dst}\\" /E /I"` | `Copy-Item -Recurse "{src}" "{dst}"` |
| `move` | `mv {tmp} {target}` | `cmd /c "move /y \\"{tmp}\\" \\"{target}\\""` | `Move-Item -Force "{tmp}" "{target}"` |
| `chown` | `chown {user} {path}` | Not supported | Not supported |
| `chown_r` | `chown -R {user}:{user} {path}` | Not supported | Not supported |
| `list_dir` | `ls -la {path}/` | `dir "{path}" /Q` | `Get-ChildItem -Path "{path}" \| Format-List` |
| `install` | Preserves owner/mode | Move only | Move only |
| `tmp_prefix` | `/tmp` | `%TEMP%` | `$env:TEMP` |

### Feature Differences

| Feature | bash | cmd | powershell |
|---------|------|-----|------------|
| Sudo execution | ✅ | ❌ | ❌ |
| Permission management (chmod/chown) | ✅ | ❌ | ❌ |
| File owner check | ✅ | ❌ | ❌ |
| Pipe commands | ✅ | ✅ | ✅ |
| Multi-line commands | ✅ | ✅ | ✅ |
| Script execution | ✅ | ✅ | ✅ |
| Directory operations | ✅ | ✅ | ✅ |
| File upload/download | ✅ | ✅ | ✅ |

## Security: Command Filtering + Path Restrictions

### Command Filtering

Protect your servers with regex whitelist/blacklist:

```yaml
# Server YAML override
server:
  blacklist:
    - "rm -rf"                 # Block rm -rf on this server
  whitelist:
    - "ls|df|docker|tail"      # Only allow these commands
  command_template: "cd /opt/app && <command>"  # Auto cd then execute
```

- **Blacklist**: Command matching any pattern is rejected.
- **Whitelist**: When configured (non-empty), only commands matching at least one pattern are executed.
- **Command template**: Wraps all commands. Use `<command>` as placeholder.

### Path Restrictions

Restrict upload/download paths:

```yaml
server:
  allowed_local_paths:
    - "/home/bit/scripts"      # Allowed local upload paths
  allowed_remote_paths:
    - "/home/bit/scripts"      # Allowed remote upload/download paths
```

- Empty (default): No restriction.
- When set: Only paths under specified directories are allowed.
- Applies to both `upload` and `download`.

## File Transfer: Upload + Download

### Upload

```bash
# Upload single script
python cli.py my-server upload script.sh

# Upload and run immediately
python cli.py my-server upload script.sh -r

# Custom remote filename
python cli.py my-server upload script.sh -n remote-name.sh

# Upload all alias scripts
python cli.py my-server upload-all
```

### Download

```bash
# Download single file
python cli.py my-server download /remote/file.log ./local/file.log

# Set timeout
python cli.py my-server download /remote/file.log ./local/file.log -t 600

# Recursive directory download
python cli.py my-server download /var/log ./logs

# Download only .log files
python cli.py my-server download /var/log ./logs -p "\\.log$"
```

Downloads obey `allowed_remote_paths` restrictions. Directory downloads preserve remote directory structure locally. Files not matching the `pattern` regex are skipped.

### Sudo File Transfer

```bash
# Sudo upload (via /tmp staging + sudo mv)
python cli.py my-server upload script.sh -s

# Sudo download (via /tmp staging + chown + SFTP)
python cli.py my-server download /root/secret.txt ./secret.txt -s
```

## Path Resolution

### Relative Paths

Relative script paths resolve against the YAML file's directory:

```yaml
# servers/prod.yml
aliases:
  - name: deploy
    script: deploy.sh           # → servers/deploy.sh
  - name: backup
    script: ./scripts/backup.sh # → servers/scripts/backup.sh
```

### Absolute Paths

Absolute path scripts upload to `scripts_dir/external/`:

```yaml
aliases:
  - name: external-tool
    script: /opt/tools/tool.sh  # → /home/user/scripts/external/tool.sh
```

### Windows Paths

Windows paths auto-convert to WSL format (Linux environment):

```
D:\agents\servers\script.sh → /mnt/d/agents/servers/script.sh
```

## Example Scripts

### Deployment Script

```bash
#!/usr/bin/env bash
set -eo pipefail
cd /opt/my-app
git pull origin main 2>&1 | tail -5
npm install && npm run build
systemctl restart my-app
echo "Deployment complete"
```

### Health Check Script

```bash
#!/usr/bin/env bash
echo "--- Service Status ---"
systemctl status my-app --no-pager | head -10
echo "--- Disk Usage ---"
df -h /
echo "--- Memory ---"
free -h
```

## Feature Summary

| Category | Feature | Description | Config / API |
|----------|---------|-------------|--------------|
| **Connection** | Direct SSH | Connect to remote server | `server.host`, `server.port` |
| | Password auth | Password login | `server.password` |
| | Key auth | SSH key login (optional passphrase) | `server.key`, `server.key_password` |
| | SSH jump host | Reach a target through one named bastion | `server.jump_host` |
| | HTTP CONNECT / SOCKS5 proxy | Explicit proxy, no direct fallback | `config.yaml` proxy / `server.proxy` |
| | Connection pool | Auto-reuse, 60s keepalive | Global `pool.get(name)` |
| **Command Execution** | `run()` | Execute command, optional sudo, real-time streaming | `ssh_run` / CLI `run -s` |
| | `run_alias()` | Execute predefined alias (streams output) | `ssh_alias.server.name` / CLI `alias` |
| | Command template | Wrap all commands (e.g., auto cd) | `server.command_template` |
| | Command filtering | Regex whitelist/blacklist | `server.blacklist` / `server.whitelist` |
| **Script Management** | `upload_script()` | Upload script | CLI `upload` |
| | `run_script()` | Execute uploaded script | `ssh_run_script` / CLI `run-script` |
| | `upload_all_scripts()` | Upload all scripts from alias definitions | CLI `upload-all` |
| | `list_scripts()` | List remote uploaded scripts | CLI `list-scripts` |
| | Upload then run | `upload_script(run_immediately=True)` | `ssh_upload_script` / CLI `upload -r` |
| **File Transfer** | `download()` | Download single file or recursive directory | `ssh_download` / CLI `download` |
| | Download filtering | Regex filter filenames | `pattern` parameter |
| | Download count | Return file count for directory download | `count` field |
| | Download sudo | Read root-owned files (via /tmp staging) | `sudo: true` parameter |
| | Download overwrite control | Skip existing local files | `overwrite` parameter (default: true) |
| | Upload overwrite control | Skip existing remote scripts | `overwrite` parameter (default: true) |
| | Path restrictions | Restrict upload/download paths | `server.allowed_local_paths` / `server.allowed_remote_paths` |
| **Alias System** | Inline alias | Execute command string directly | `aliases[].inline` |
| | Script alias | Upload + execute script file | `aliases[].script` |
| | Sudo alias | Execute with root privileges | `aliases[].sudo: true` |
| | Inheritance | Share aliases via `extends` | `extends: [_shared/common.yml]` |
| **MCP Tools** | Dynamic tools | Each alias auto-exposed as `ssh_alias.server.name` | Auto-generated at runtime |
| | Configuration UI | Manage multiple servers without reading stored passwords | `ssh_open_config` |
| | Tool discovery | `ssh_list_servers` / `ssh_list_aliases` | Static tools |
| | Read/write markers | Tools marked readOnly/destructive | Auto-set |
| **Security** | Command blacklist | Regex pattern blocks commands | `server.blacklist` |
| | Command whitelist | Only allow matching commands | `server.whitelist` |
| | Path restrictions | Restrict upload/download paths | `server.allowed_local_paths` / `server.allowed_remote_paths` |
| | Proxy error log | Proxy failures logged to file | `proxy_error.log` |
