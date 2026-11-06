# AI Agent Installation Guide

> This document is written for AI agents. Follow the steps in order to complete installation.

## 1. Clone the Repository

Clone directly into your agent's skills directory:

**Claude Code:**
```bash
git clone https://github.com/jdassd/ssh-alias-mcp.git ~/.claude/skills/ssh-alias-mcp
```

**Codex CLI:**
```bash
git clone https://github.com/jdassd/ssh-alias-mcp.git ~/.codex/skills/ssh-alias-mcp
```

**OpenCode:**
```bash
git clone https://github.com/jdassd/ssh-alias-mcp.git ~/.opencode/skills/ssh-alias-mcp
```

Requires: Python ≥ 3.9

## 2. Install Dependencies

```bash
pip install -r <skills-dir>/ssh-alias-mcp/requirements.txt
```

Only three dependencies: `paramiko`, `pyyaml`, `pysocks`. Nothing else.

## 3. Register the MCP Server

Add the following entry to your agent's MCP configuration:

**Claude Code** (`.claude/settings.local.json`):
```json
{
  "mcpServers": {
    "ssh-alias-mcp": {
      "command": "python",
      "args": ["<skills-dir>/ssh-alias-mcp/mcp_server.py"]
    }
  }
}
```

**Codex CLI / OpenCode** (`mcp.json`):
```json
{
  "mcpServers": {
    "ssh-alias-mcp": {
      "command": "python",
      "args": ["<skills-dir>/ssh-alias-mcp/mcp_server.py"]
    }
  }
}
```

Replace `<skills-dir>` with the actual path. For Claude Code global, it's `~/.claude/skills`. For Codex, `~/.codex/skills`. For OpenCode, `~/.opencode/skills`.

## 4. Verify

After restarting your AI agent, confirm the MCP tools are loaded:

```bash
# Quick CLI check (optional but recommended)
python <skills-dir>/ssh-alias-mcp/cli.py list-servers
```

If `servers/` already contains `.yml` configs, the CLI will list them. An empty list is fine too.

The following MCP tools should appear in your agent session:
- `ssh_open_config`
- `ssh_list_servers`
- `ssh_run`
- `ssh_upload_script`
- `ssh_run_script`
- `ssh_run_alias`
- `ssh_list_aliases`
- `ssh_download`
- `ssh_list_scripts`
- `ssh_upload_all_scripts`
- `ssh_alias.{server}.{name}` (one per alias, e.g. `ssh_alias.prod-01.deploy`)

## 5. Add Servers

Ask the agent to open SSH server settings in the default browser, or run `python cli.py open-config` and keep that process running while editing. The page uses the existing config files and leaves stored passwords unchanged when the password field is empty. For Codex plugin installs, configuration stays in the stable plugin data directory across program updates. You can also create `.yml` files under `servers/`. Minimal config:

```yaml
server:
  host: "your-server-ip"
  user: "your-username"
  password: "your-password"
```

Full configuration reference: [REFERENCE.md](REFERENCE.md)

Done. Your AI agent can now manage servers via MCP. Defining a few aliases makes the experience much better — see the README for examples.
