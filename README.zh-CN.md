# ssh-alias-mcp

[English](README.md) | [中文](README.zh-CN.md) &nbsp;·&nbsp; [SKILL](SKILL.md) &nbsp;·&nbsp; [详细参考](REFERENCE.zh-CN.md) &nbsp;·&nbsp; [CLI](CLI_USAGE.zh-CN.md) &nbsp;·&nbsp; [安装](INSTALL.zh-CN.md)

AI 驱动的服务器运维工具。YAML 配置服务器，执行命令、部署脚本、管理文件 — AI Agent 和命令行统一接口。

## ✨ 亮点

- **浏览器配置界面** — 在本机网页中添加、修改、删除多台服务器
- **一套配置，三种 Shell** — `bash`、`cmd`、`powershell` 通过命令模板自动适配
- **SSH 跳板机** — 通过命名的堡垒机配置和 `direct-tcpip` 访问内网目标机
- **Alias 系统** — 一行 YAML = 一个 AI 技能，自动暴露为 MCP 工具
- **MCP + CLI** — 同一套配置、同一个连接池，AI 和人工共用

## 快速开始

```bash
# AI Agent (MCP)
claude mcp add ssh-alias-mcp python <路径>/mcp_server.py

# CLI
python cli.py my-server run "uptime"
python cli.py my-server alias deploy
```

在对话中说“打开 SSH 服务器配置”，即可在默认浏览器打开配置页。网页仅监听 `127.0.0.1`，继续使用已有配置文件；已保存的密码不会返回给 Agent，也不会在表单中回显。自动打开失败时，可使用 `ssh_open_config` 返回的 `url` 手动打开。

也可直接运行 `python cli.py open-config`。使用网页期间保持该进程运行，结束时按 Ctrl+C 关闭；通过 MCP 打开时，网页服务随 MCP 进程运行。

## 文档

- [SKILL.md](SKILL.md) — AI Agent 技能定义（MCP 工具、快速参考）
- [REFERENCE.zh-CN.md](REFERENCE.zh-CN.md) — 服务器配置、别名、安全、文件传输（中文版）
- [CLI_USAGE.zh-CN.md](CLI_USAGE.zh-CN.md) — CLI 命令参考（中文版）
- [INSTALL.zh-CN.md](INSTALL.zh-CN.md) — AI Agent 安装指南（中文版）

## 一句话 Agent 安装（把下面这句话复制给你的 Agent）

安装请阅读 https://github.com/jdassd/ssh-alias-mcp/blob/main/INSTALL.zh-CN.md，按说明配置好 SKILL 及安装 MCP 服务。

## 使用场景

### 场景 1：并行部署 5 台服务器

```yaml
# _shared/common.yml
aliases:
  - name: deploy
    script: deploy.sh
    sudo: true
```

**AI 工作流：** 你说"部署到所有生产服务器" → AI 读取服务器列表 → 对每台服务器调用 `ssh_run`、`ssh_run_alias` 或 `ssh_alias.prod-01.deploy` 这类动态 alias 工具并行部署 → 汇报结果。无需 SSH 连接信息，无需密码交互。

### 场景 2：AI 分析死机原因

```yaml
aliases:
  - name: crash-check
    inline: "journalctl -xe --since '1 hour ago' && dmesg -T | tail -100 && free -h && df -h /"
```

**之前：** SSH 进服务器 → 手动查日志 → 搜索内核 panic → 分析 core dump → 几个小时
**AI 工作流：** 你说"服务器崩了，查原因" → AI 用 `ssh_alias.prod-01.crash-check` 抓取日志 → 分析崩溃原因 → 定位 OOM killer → 给出修复建议

### 场景 3：紧急故障排查

```yaml
aliases:
  - name: check
    inline: "docker logs --tail 50 my-app && df -h / && free -h"
```

**之前：** 打开终端 → SSH → 敲命令 → 复制输出 → 分析
**AI 工作流：** 你说"程序崩了，帮我看看" → AI 用 `ssh_run`、`ssh_run_alias` 或动态 alias 工具获取日志和指标 → 分析崩溃原因 → 给出修复建议

### 场景 4：跨平台部署

```yaml
# Linux 服务器
server:
  host: "192.168.1.100"
  shell: bash

# Windows 服务器
server:
  host: "10.0.0.50"
  shell: powershell
```

**AI 工作流：** 你说"部署到 Linux 和 Windows 服务器" → AI 读取服务器配置 → 对每台服务器使用 `ssh_run`、`ssh_run_alias` 或动态 alias 工具 → 工具自动适配 bash/powershell 命令 → 统一汇报结果

## 链接

- [GitHub](https://github.com/jdassd/ssh-alias-mcp)
