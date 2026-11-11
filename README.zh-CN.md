# ssh-alias-mcp

[English](README.md) | [中文](README.zh-CN.md) &nbsp;·&nbsp; [SKILL](SKILL.md) &nbsp;·&nbsp; [详细参考](REFERENCE.zh-CN.md) &nbsp;·&nbsp; [CLI](CLI_USAGE.zh-CN.md) &nbsp;·&nbsp; [安装](INSTALL.zh-CN.md)

AI 驱动的服务器运维工具。YAML 配置服务器，执行命令、部署脚本、管理文件 — AI Agent 和命令行统一接口。

## ✨ 亮点

- **浏览器配置界面** — 在本机网页中添加、修改、删除多台服务器
- **一套配置，三种 Shell** — `bash`、`cmd`、`powershell` 通过命令模板自动适配
- **SSH 跳板机** — 通过命名的堡垒机配置和 `direct-tcpip` 访问内网目标机
- **HTTP CONNECT 代理** — SSH 与 SFTP 共用带认证的 HTTP 隧道；旧数据缺少代理配置时继承 OpenVPN 代理默认值，可明确直连或继续使用原有 SOCKS5
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

也可直接运行 `python cli.py open-config`。使用网页期间保持该进程运行，结束时按 Ctrl+C 关闭；通过 MCP 打开时，网页服务由 MCP 进程管理，空闲回收后再次打开即可。

共享 HTTP 代理默认使用 Windows 的 `127.0.0.1:18080` 或 Linux/Cage 的 `127.0.0.1:18081`。凭据保存在仓库外的 `~/.config/openvpn-http-proxy/proxy-auth.json`（username/password），也可通过 `VPN_HTTP_PROXY_AUTH_FILE` 指定；`VPN_HTTP_PROXY_URL` 覆盖默认地址。已有明确代理配置仍优先，包括 `enabled: false` 直连；详细说明见[全局配置参考](REFERENCE.zh-CN.md#全局配置configyaml)。代理失败不会自动回退到直连。

## 性能与资源上限

- YAML 解析结果、继承配置和动态 alias 索引按配置版本缓存。程序内保存立即使缓存失效；外部文件修改通过文件签名识别，检查间隔为 250ms。
- MCP 远程操作采用有界执行器：8 个 worker，最多接纳 32 个请求，同一服务器最多同时执行 4 个操作。元数据使用独立执行器：2 个 worker、最多接纳 16 个请求；协议控制请求保持响应。队列满时明确返回繁忙错误，避免无限堆积。
- 连接按需建立并复用，默认空闲 300 秒后回收。活跃操作和跳板机依赖结束前保留其传输连接。全局 `config.yaml` 中的 `server.max_connections`（默认 64）与 `server.idle_timeout`（秒）可调整连接缓存上限和空闲期限。
- 命令输出在有数据时持续读取，stdout 与 stderr 的返回内容合计默认最多 4 MiB。可通过全局 `server.max_output_bytes`，或单台服务器 YAML 的 `max_output_bytes` 调整。达到返回上限后继续消费远端输出，命令正常执行；命令超时则关闭通道并标记输出不完整。结果附带字节数、截断、超时、建连与执行耗时；不会自动重试远程命令。
- 浏览器配置页立即初始化，复用带版本的脱敏快照。网页服务空闲 30 分钟后回收，下次调用 `ssh_open_config` 自动重新建立。

现有服务器文件、名称、alias 和凭据保持不变。修改配置时，旧连接会在活跃操作完成后退出，后续请求使用新配置。

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
