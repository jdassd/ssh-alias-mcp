# ssh-alias-mcp

[English](README.md) | [中文](README.zh-CN.md) &nbsp;·&nbsp; [SKILL](SKILL.md) &nbsp;·&nbsp; [REFERENCE](REFERENCE.md) &nbsp;·&nbsp; [CLI](CLI_USAGE.md) &nbsp;·&nbsp; [INSTALL](INSTALL.md)

AI-driven server operations tool. Configure servers in YAML, execute commands, deploy scripts, and manage files — all through a unified interface for AI Agents and CLI.

## ✨ Highlights

- **Browser configuration UI** — Open local settings to add, edit, and delete multiple servers
- **One config, three shells** — `bash`, `cmd`, `powershell` auto-adapted via command templates
- **SSH jump hosts** — Reach private targets through a named bastion config using `direct-tcpip`
- **HTTP CONNECT proxy** — Authenticated HTTP tunnels for SSH and SFTP; old missing proxy settings inherit the OpenVPN proxy default, with explicit direct/SOCKS5 overrides
- **Alias system** — One YAML line = one AI skill, auto-exposed as MCP tools
- **MCP + CLI** — Same config, same connection pool, shared by AI and humans

## Quick Start

```bash
# AI Agent (MCP)
claude mcp add ssh-alias-mcp python <path>/mcp_server.py

# CLI
python cli.py my-server run "uptime"
python cli.py my-server alias deploy
```

Ask the agent to “open SSH server settings” to open the configuration page in your default browser. The page runs on `127.0.0.1` and uses the same saved configuration files; stored passwords are never returned to the agent or displayed in the form. If the browser cannot be launched automatically, open the `url` returned by `ssh_open_config`.

You can also run `python cli.py open-config` directly. Keep that process running while using the page, then press Ctrl+C to stop it. With MCP, the page is managed by the MCP process and can be reopened after idle expiry.

The shared HTTP proxy defaults to `127.0.0.1:18080` on Windows and `127.0.0.1:18081` on Linux/Cage. Store its credentials outside the repository in `~/.config/openvpn-http-proxy/proxy-auth.json` (`username` and `password`), or set `VPN_HTTP_PROXY_AUTH_FILE`. `VPN_HTTP_PROXY_URL` overrides the default endpoint. Explicit existing proxy settings remain authoritative, including `enabled: false`; see the [proxy configuration reference](REFERENCE.md#global-configuration-configyaml). A failed proxy connection never falls back to direct TCP.

For old installations whose `VPN_HTTP_PROXY_URL` still contains the former default `http://192.168.100.40:18080`, the shared default resolver automatically uses the stable loopback endpoint for the current platform. Other URLs and explicit per-server/global proxy hosts are preserved. Set `VPN_HTTP_PROXY_KEEP_LEGACY_ENDPOINT=1` if that exact old address is intentionally a remote proxy.

If a previous backup installer wrote that default into its managed global `config.yaml`, rerun the updated backup installer to migrate its installed default. The runtime resolver does not rewrite explicit configuration hosts.

## Performance and resource limits

- Parsed YAML, inherited settings, and dynamic alias indexes are cached by configuration revision. Programmatic saves invalidate the cache immediately; external edits are detected by file signatures, checked at most every 250ms.
- Remote MCP operations use a bounded executor (8 workers, at most 32 admitted requests and 4 running operations per server). Metadata uses a separate executor (2 workers, at most 16 admitted requests); protocol control requests remain responsive. A full lane returns an explicit busy error rather than accumulating work indefinitely.
- Connections are created on demand, reused, and retired after 300 seconds idle. Active operations and jump-host dependencies keep their transport alive until they finish. Global `config.yaml` settings `server.max_connections` (default 64) and `server.idle_timeout` (seconds) control the connection cache.
- Command output is drained without a fixed sleep after each block. Returned stdout and stderr share a 4 MiB budget, configurable with `server.max_output_bytes` globally or `max_output_bytes` in a server YAML. Reaching the output budget does not stop the command; excess output is consumed and omitted. A command timeout closes its channel and reports incomplete output. Results include byte counts, truncation, timeout, connection time, and total execution time; remote commands are never retried automatically.
- Browser settings initialize without a fixed delay and reuse versioned, redacted snapshots. An idle settings server closes after 30 minutes and is recreated by the next `ssh_open_config` call.

These changes preserve existing server files, names, aliases, and stored credentials. Configuration changes retire old connections after active operations finish; the next request uses the new settings.

## Documentation

- [SKILL.md](SKILL.md) — AI Agent skill definition (MCP tools, quick reference)
- [REFERENCE.md](REFERENCE.md) — Server configuration, aliases, security, file transfer
- [CLI_USAGE.md](CLI_USAGE.md) — CLI commands reference
- [INSTALL.md](INSTALL.md) — AI Agent installation guide

## Agent Setup in One Sentence (Copy It To Your Agents)

Read https://github.com/jdassd/ssh-alias-mcp/blob/main/INSTALL.md — set up the SKILL and install the MCP service as described.

## Real-World Scenarios

### Scenario 1: Deploy to 5 servers in parallel

```yaml
# _shared/common.yml
aliases:
  - name: deploy
    script: deploy.sh
    sudo: true
```

**AI workflow:** You say "deploy to all prod servers" → AI reads server list → calls `ssh_run`, `ssh_run_alias`, or a dynamic alias tool such as `ssh_alias.prod-01.deploy` on each server in parallel → reports result. No SSH boilerplate, no password prompts.

### Scenario 2: AI analyzes crash cause

```yaml
aliases:
  - name: crash-check
    inline: "journalctl -xe --since '1 hour ago' && dmesg -T | tail -100 && free -h && df -h /"
```

**Before:** SSH into server → manually check logs → search for kernel panic → analyze core dump → hours later
**After:** Tell AI "server crashed, check why" → AI calls `ssh_alias.prod-01.crash-check` → analyzes logs → identifies OOM killer → suggests fix

### Scenario 3: Emergency troubleshooting

```yaml
aliases:
  - name: check
    inline: "docker logs --tail 50 my-app && df -h / && free -h"
```

**Before:** Open terminal → SSH → type commands → copy output → analyze
**AI workflow:** You say "my app is slow, check it" → AI uses `ssh_run`, `ssh_run_alias`, or a dynamic alias tool to grab logs and metrics → identifies bottleneck → suggests fix

### Scenario 4: Cross-platform deployment

```yaml
# Linux server
server:
  host: "192.168.1.100"
  shell: bash

# Windows server
server:
  host: "10.0.0.50"
  shell: powershell
```

**AI workflow:** You say "deploy to both Linux and Windows servers" → AI reads server configs → uses `ssh_run`, `ssh_run_alias`, or dynamic alias tools on each → tool auto-adapts bash/powershell commands → reports unified result

## Links

- [GitHub](https://github.com/jdassd/ssh-alias-mcp)
