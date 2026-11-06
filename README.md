# ssh-alias-mcp

[English](README.md) | [中文](README.zh-CN.md) &nbsp;·&nbsp; [SKILL](SKILL.md) &nbsp;·&nbsp; [REFERENCE](REFERENCE.md) &nbsp;·&nbsp; [CLI](CLI_USAGE.md) &nbsp;·&nbsp; [INSTALL](INSTALL.md)

AI-driven server operations tool. Configure servers in YAML, execute commands, deploy scripts, and manage files — all through a unified interface for AI Agents and CLI.

## ✨ Highlights

- **Browser configuration UI** — Open local settings to add, edit, and delete multiple servers
- **One config, three shells** — `bash`, `cmd`, `powershell` auto-adapted via command templates
- **SSH jump hosts** — Reach private targets through a named bastion config using `direct-tcpip`
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

You can also run `python cli.py open-config` directly. Keep that process running while using the page, then press Ctrl+C to stop it. With MCP, the page stays available while the MCP server is running.

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
