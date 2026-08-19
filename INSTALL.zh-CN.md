# AI Agent 安装指南

> 本文档专为 AI Agent 编写。按顺序执行以下步骤即可完成安装。

## 1. 克隆仓库

直接 clone 到 agent 的 skills 目录下：

**Claude Code：**
```bash
git clone https://github.com/jdassd/ssh-alias-mcp.git ~/.claude/skills/ssh-alias-mcp
```

**Codex CLI：**
```bash
git clone https://github.com/jdassd/ssh-alias-mcp.git ~/.codex/skills/ssh-alias-mcp
```

**OpenCode：**
```bash
git clone https://github.com/jdassd/ssh-alias-mcp.git ~/.opencode/skills/ssh-alias-mcp
```

要求：Python ≥ 3.9

## 2. 安装依赖

```bash
pip install -r <skills-dir>/ssh-alias-mcp/requirements.txt
```

仅三个依赖：`paramiko`、`pyyaml`、`pysocks`。无其他要求。

## 3. 注册 MCP 服务器

在 agent 的 MCP 配置文件中添加以下条目：

**Claude Code**（`.claude/settings.local.json`）：
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

**Codex CLI / OpenCode**（`mcp.json`）：
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

将 `<skills-dir>` 替换为实际路径。Claude Code 全局安装路径为 `~/.claude/skills`；Codex 为 `~/.codex/skills`；OpenCode 为 `~/.opencode/skills`。

## 4. 验证

重启 AI Agent 后，确认 MCP 工具已加载：

```bash
# 使用 CLI 验证配置是否正常（非必需，但建议执行）
python <skills-dir>/ssh-alias-mcp/cli.py list-servers
```

如果 `servers/` 目录已有 `.yml` 配置，CLI 会列出服务器信息。没有服务器配置文件也会正常运行，仅返回空列表。

Agent 会话中应出现以下 MCP 工具：
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
- `ssh_alias.{server}.{name}`（每个别名一个，如 `ssh_alias.prod-01.deploy`）

## 5. 添加服务器

可以让 Agent“打开 SSH 服务器配置”，也可以在 `servers/` 目录下创建 `.yml` 文件。最小配置：

```yaml
server:
  host: "your-server-ip"
  user: "your-username"
  password: "your-password"
```

完整配置参考：[REFERENCE.zh-CN.md](REFERENCE.zh-CN.md)

完成。现在你的 AI Agent 可以通过 MCP 管理服务器。定义几个别名会让体验更好 — 参考 README 中的示例。
