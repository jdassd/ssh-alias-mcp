# REFERENCE — 服务器配置、别名、安全与文件传输

## 架构

```
┌──────────────────────────────────────────────────────┐
│  AI Agent (Claude Code / Codex / OpenCode / ...)      │
│  └── MCP 工具调用 (ssh_run, ssh_alias.*)         │
└──────────────┬───────────────────────────────────────┘
               │ JSON-RPC over stdio
┌──────────────▼───────────────────────────────────────┐
│  mcp_server.py  (MCP stdio 服务器)                   │
│  └── 动态将别名暴露为 MCP 工具                         │
└──────────────┬───────────────────────────────────────┘
               │
┌──────────────▼───────────────────────────────────────┐
│  ssh_client.py  (共享核心)                            │
│  ├── 连接池 (连接复用，60s 保活)                       │
│  ├── SSH 跳板机转发 (direct-tcpip)                    │
│  ├── HTTP CONNECT / SOCKS5 代理，不回退直连                        │
│  └── SFTP 脚本上传与下载                              │
└──────────────┬───────────────────────────────────────┘
               │ SSH
┌──────────────▼───────────────────────────────────────┐
│  远程服务器 (Linux / Windows)                         │
└──────────────────────────────────────────────────────┘
```

## 目录结构

```
├── <skills-dir>/
│   └── ssh-alias-mcp/         # 核心代码
│       ├── SKILL.md           # AI Agent 技能定义
│       ├── REFERENCE.md       # 详细参考文档（英文版）
│       ├── REFERENCE.zh-CN.md # 详细参考文档（中文版）
│       ├── CLI_USAGE.md       # CLI 命令参考（英文版）
│       ├── CLI_USAGE.zh-CN.md # CLI 命令参考（中文版）
│       ├── INSTALL.md         # AI Agent 安装指南（英文版）
│       ├── INSTALL.zh-CN.md   # AI Agent 安装指南（中文版）
│       ├── cli.py             # CLI 入口
│       ├── mcp_server.py      # MCP stdio 服务器
│       ├── ssh_client.py      # 核心模块（SSH 连接、代理、连接池）
│       ├── config_web.py      # 仅本机访问的配置网页服务
│       ├── ui/config.html     # 服务器配置表单
│       ├── ui/browser.js      # 带会话校验的浏览器通信
│       ├── config.yaml        # 全局配置（servers_dir、代理、超时）
│       └── requirements.txt   # Python 依赖
│
└── servers/                   # 服务器配置目录（路径在 config.yaml 中配置）
    ├── my-server.yml          # 服务器连接信息 + 别名定义
    ├── _shared/common.yml     # 共享别名（通过 extends 继承）
    └── my-server/             # 此服务器的本地 shell 脚本（.sh）
```

> 服务器配置 `servers/` 与技能目录分离，便于管理和凭证隔离。路径通过 `config.yaml` 中的 `servers_dir` 配置（支持绝对路径或相对路径）。

## 全局配置（`config.yaml`）

```yaml
# servers/ 目录路径（绝对路径或相对于此文件的路径）
servers_dir: "../servers"

proxy:
  enabled: true               # 明确设为 false 时直连
  type: http                  # HTTP CONNECT；也兼容原有 socks5
  # 省略 host/port：Windows 127.0.0.2:18080；Linux 127.0.0.1:18081
  # auth_file: ~/.config/openvpn-http-proxy/proxy-auth.json
  timeout: 30                 # 代理连接超时（秒）

server:
  timeout: 30                 # 默认 SSH 连接超时（秒）
  auto_reconnect: true        # 连接丢失时自动重连
  reconnect_interval: 5       # 重连尝试间隔（秒）

security:
  blacklist: []               # 阻止命令的正则表达式
  whitelist: []               # 允许命令的正则表达式（空 = 无限制）
  command_template: ""        # 包装所有命令的命令模板
```

HTTP CONNECT 承载 SSH TCP 连接，命令、别名、SFTP 上传下载及跳板链的首跳均使用同一代理连接层。代理失败会明确报错，不会静默回退直连。需要直连的服务器可配置 `server.proxy: {enabled: false}`。

`server.proxy` 缺失或为 null 时继承全局；全局也没有配置时默认使用上述带认证的 HTTP 代理，旧服务器数据不必逐条迁移。已有明确 `enabled: false` 的配置仍然直连；已有未指定 `type`、仅含 host/port 的代理对象保持 SOCKS5 语义。使用 `jump_host` 的目标通过跳板连接，代理应配置在跳板机上。

HTTP 配置支持 `host`、`port`、`username`、`password`、`auth_file`、`timeout`。默认在连接时从 `~/.config/openvpn-http-proxy/proxy-auth.json` 读取 username/password；明确配置的用户名密码优先。自定义无认证代理可设 `auth_file: null`。环境变量 `VPN_HTTP_PROXY_URL=http://host:port`、`VPN_HTTP_PROXY_AUTH_FILE=/path/to/proxy-auth.json` 覆盖共享默认值，单服务器或全局中显式字段优先。配置界面不会返回已保存的代理密码，错误日志也不记录认证字段。

旧安装器生成的 `VPN_HTTP_PROXY_URL=http://192.168.100.40:18080` 和 `http://127.0.0.1:18080` 会先经过 URL 校验，再映射到当前平台的默认地址。Windows 使用专用 `127.0.0.2:18080`，避免 Cage 自动端口转发占用 `127.0.0.1:18080`。此兼容规则仅作用于共享默认层，其他 URL 和单服务器、全局中显式 host 均不改写。需要在环境变量中有意保留历史端点时，设置 `VPN_HTTP_PROXY_KEEP_LEGACY_ENDPOINT=1`。

`auto_reconnect` 用于控制连接请求失败后的后台重连。连续连接失败少于 5 次时，
按 `reconnect_interval` 指定的秒数重试；第 5 次失败后降为每 60 秒重试一次。
即使处于冷却期，新的 CLI 或 MCP 请求也会立即触发一次连接尝试。
设置 `auto_reconnect: false` 可完全关闭后台重连。

## 服务器配置（`{servers_dir}/{name}.yml`）

在 `servers/` 目录中为每台服务器创建 `.yml` 文件：

```yaml
# my-server.yml

server:
  # === 必填字段 ===
  host: "your.host.com"         # 服务器 IP 或主机名
  user: "username"               # SSH 登录用户名

  # === 显示信息 ===
  name: "My Server"              # 显示名称
  desc: "Application server"    # 描述
  group: "dept/team"            # 分组标识符（多级路径用于排序）

  # 认证方式（二选一）
  password: "your-password"      # 密码认证
  # key: "/path/to/private_key"     # 或密钥认证
  # key_password: "passphrase"      # 密钥密码（可选）
  sudo_password: "sudo-pass"     # Sudo 密码（可选，默认为 password）

  # === 可选字段 ===
  port: 22                       # SSH 端口（默认 22）
  timeout: 30                    # SSH 连接超时（秒，默认 30）
  scripts_dir: "/home/user/scripts"  # 远程脚本目录
  shell: "bash"                  # Shell 类型（bash / cmd / powershell，默认 bash）
  system: "Ubuntu 24.04 LTS"    # 操作系统信息

  # SSH 跳板机（可选；填写另一份服务器配置的名称）
  # jump_host: "bastion-prod"

  # 服务器级代理覆盖（可选，覆盖全局 config.yaml）
  proxy:
    host: "127.0.0.1"
    port: 10808

  # 安全：命令过滤（覆盖全局 config.yaml）
  blacklist:
    - "rm -rf|mkfs|dd "          # 正则表达式，匹配则阻止
  whitelist:
    - "ls|df|docker|tail"        # 正则表达式，仅允许匹配的命令
  command_template: "cd /opt/app && <command>"  # 自动包装命令

  # 上传/下载路径限制（空 = 无限制）
  allowed_local_paths:
    - "/home/bit/scripts"
  allowed_remote_paths:
    - "/home/bit/scripts"

# 快捷命令 — 自动暴露为 MCP 工具
aliases:
  # 脚本类型：上传本地脚本文件后执行
  - name: deploy
    script: app-deploy.sh        # 脚本路径，相对于 YAML 文件目录
    desc: "Deploy application"
    timeout: 600                 # 命令超时（秒），默认 300
    sudo: true                   # 可选，以 root 权限执行

  # 内联类型：直接在远程执行命令
  - name: logs
    inline: "docker logs --tail 100 my-app"
    desc: "View logs"
    timeout: 10
```

## SSH 跳板机（堡垒机）

将堡垒机和目标机分别定义为独立的服务器配置。目标机的 `jump_host` 填写
堡垒机配置文件名（不带 `.yml`）：

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

对 `internal-app` 的所有 CLI 和 MCP 操作都会先连接 `bastion-prod`，再打开
通往 `10.0.0.20:22` 的 SSH `direct-tcpip` 通道，最后在该通道上建立目标机
SSH 会话。堡垒机和目标机分别认证；目标机凭据只用于目标 SSH 握手。

- 仅支持一层跳板机。自引用、循环引用以及自身还配置了 `jump_host` 的堡垒机都会被拒绝。
- 配置了 `jump_host` 的目标机不能同时设置 `server.proxy`。若需
  SOCKS5 → 堡垒机 → 目标机，请把代理配置在堡垒机上。
- 跳板机连接失败时不会绕过跳板机直连目标机。
- 堡垒机的 SSH 服务必须允许 TCP 转发（例如 OpenSSH 服务端配置
  `AllowTcpForwarding yes`）。

## 共享别名继承（`extends`）

### 三级继承链

```
config.yaml (全局默认值：代理、超时)
    ↓ extends
_shared/*.yml (共享连接信息、共享别名)
    ↓ extends
服务器 YAML (你的服务器 — 覆盖 + 追加)
```

### 多重继承

服务器 YAML 可以从多个文件继承 — 层叠式配置组合：

```yaml
# my-server.yml
extends:
  - _shared/conn-base.yml    # ① 共享主机、用户、scripts_dir
  - _shared/common.yml       # ② 共享别名

server:
  host: "198.51.100.10"      # 覆盖 ① 的主机
  port: 22
  password: "xxx"

aliases:                      # 追加 — 本地别名胜承继承的别名
  - name: deploy
    script: deploy.sh
```

### 合并规则

| 字段 | 合并行为 |
|------|---------|
| `server` | 浅合并 — 本地覆盖基础 |
| `aliases` | 全部继承自基础。同名覆盖，不同名追加 |
| `security`（whitelist/blacklist/command_template） | 基础设置默认值，本地继承。本地存在则覆盖 |
| `proxy` | 同安全 — 本地回退到基础 |
| `allowed_local_paths` / `allowed_remote_paths` | 同安全 — 本地回退到基础 |

## Shell 类型支持

### 三种 Shell 类型

| Shell | 用途 | 命令模板 |
|-------|------|---------|
| `bash` | Linux/macOS 服务器 | 完整 bash 命令集 |
| `cmd` | Windows 服务器（CMD） | `cmd /c "..."` 包装 |
| `powershell` | Windows 服务器（PowerShell） | 原生 PowerShell 语法 |

### Shell 命令模板对比

| 模板 | bash | cmd | powershell |
|------|------|-----|------------|
| `mkdir` | `mkdir -p {path}` | `cmd /c "mkdir \\"{path}\\" 2>nul"` | `New-Item -ItemType Directory -Path "{path}" -Force` |
| `file_exists` | `test -f {path}` | `cmd /c "if exist \\"{path}\\" echo exists"` | `Test-Path "{path}"` |
| `run_script` | `bash {path}` | `cmd /c "call {path}"` | `& "{path}"` |
| `chmod` | `chmod {mode:o} {path}` | 不支持 | 不支持 |
| `stat_owner` | `stat -c '%U:%G' {path}` | 不支持 | 不支持 |
| `stat_mode` | `stat -c '%a' {path}` | 不支持 | 不支持 |
| `rm_dir` | `rm -rf {path}` | `cmd /c "rmdir /S /Q \\"{path}\\""` | `Remove-Item -Recurse -Force "{path}"` |
| `cp_r` | `cp -r {src} {dst}` | `cmd /c "xcopy /Y \\"{src}\\" \\"{dst}\\" /E /I"` | `Copy-Item -Recurse "{src}" "{dst}"` |
| `move` | `mv {tmp} {target}` | `cmd /c "move /y \\"{tmp}\\" \\"{target}\\""` | `Move-Item -Force "{tmp}" "{target}"` |
| `chown` | `chown {user} {path}` | 不支持 | 不支持 |
| `chown_r` | `chown -R {user}:{user} {path}` | 不支持 | 不支持 |
| `list_dir` | `ls -la {path}/` | `dir "{path}" /Q` | `Get-ChildItem -Path "{path}" \| Format-List` |
| `install` | 保留所有者/模式 | 仅移动 | 仅移动 |
| `tmp_prefix` | `/tmp` | `%TEMP%` | `$env:TEMP` |

### 功能差异

| 功能 | bash | cmd | powershell |
|------|------|-----|------------|
| Sudo 执行 | ✅ | ❌ | ❌ |
| 权限管理（chmod/chown） | ✅ | ❌ | ❌ |
| 文件所有者检查 | ✅ | ❌ | ❌ |
| 管道命令 | ✅ | ✅ | ✅ |
| 多行命令 | ✅ | ✅ | ✅ |
| 脚本执行 | ✅ | ✅ | ✅ |
| 目录操作 | ✅ | ✅ | ✅ |
| 文件上传/下载 | ✅ | ✅ | ✅ |

## 安全：命令过滤 + 路径限制

### 命令过滤

使用正则表达式白名单/黑名单保护服务器：

```yaml
# 服务器 YAML 覆盖
server:
  blacklist:
    - "rm -rf"                 # 阻止此服务器上的 rm -rf
  whitelist:
    - "ls|df|docker|tail"      # 仅允许这些命令
  command_template: "cd /opt/app && <command>"  # 自动 cd 后执行
```

- **黑名单**：命令匹配任何模式则被拒绝。
- **白名单**：配置后（非空），仅执行匹配至少一个模式的命令。
- **命令模板**：包装所有命令。使用 `<command>` 作为占位符。

### 路径限制

限制上传/下载路径：

```yaml
server:
  allowed_local_paths:
    - "/home/bit/scripts"      # 允许的本地上传路径
  allowed_remote_paths:
    - "/home/bit/scripts"      # 允许的远程上传/下载路径
```

- 空（默认）：无限制。
- 设置后：仅允许指定目录下的路径。
- 同时适用于 `upload` 和 `download`。

## 文件传输：上传 + 下载

### 上传

```bash
# 上传单个脚本
python cli.py my-server upload script.sh

# 上传并立即执行
python cli.py my-server upload script.sh -r

# 自定义远程文件名
python cli.py my-server upload script.sh -n remote-name.sh

# 上传所有别名脚本
python cli.py my-server upload-all
```

### 下载

```bash
# 下载单个文件
python cli.py my-server download /remote/file.log ./local/file.log

# 设置超时
python cli.py my-server download /remote/file.log ./local/file.log -t 600

# 递归目录下载
python cli.py my-server download /var/log ./logs

# 仅下载 .log 文件
python cli.py my-server download /var/log ./logs -p "\\.log$"
```

下载遵守 `allowed_remote_paths` 限制。目录下载保留远程目录结构。不匹配 `pattern` 正则表达式的文件将被跳过。

### Sudo 文件传输

```bash
# Sudo 上传（通过 /tmp 暂存 + sudo mv）
python cli.py my-server upload script.sh -s

# Sudo 下载（通过 /tmp 暂存 + chown + SFTP）
python cli.py my-server download /root/secret.txt ./secret.txt -s
```

## 路径解析

### 相对路径

相对脚本路径相对于 YAML 文件目录解析：

```yaml
# servers/prod.yml
aliases:
  - name: deploy
    script: deploy.sh           # → servers/deploy.sh
  - name: backup
    script: ./scripts/backup.sh # → servers/scripts/backup.sh
```

### 绝对路径

绝对路径脚本上传到 `scripts_dir/external/`：

```yaml
aliases:
  - name: external-tool
    script: /opt/tools/tool.sh  # → /home/user/scripts/external/tool.sh
```

### Windows 路径

Windows 路径在 Linux 环境中自动转换为 WSL 格式：

```
D:\agents\servers\script.sh → /mnt/d/agents/servers/script.sh
```

## 示例脚本

### 部署脚本

```bash
#!/usr/bin/env bash
set -eo pipefail
cd /opt/my-app
git pull origin main 2>&1 | tail -5
npm install && npm run build
systemctl restart my-app
echo "Deployment complete"
```

### 健康检查脚本

```bash
#!/usr/bin/env bash
echo "--- Service Status ---"
systemctl status my-app --no-pager | head -10
echo "--- Disk Usage ---"
df -h /
echo "--- Memory ---"
free -h
```

## 功能汇总

| 类别 | 功能 | 描述 | 配置 / API |
|------|------|------|-----------|
| **连接** | 直连 SSH | 连接远程服务器 | `server.host`, `server.port` |
| | 密码认证 | 密码登录 | `server.password` |
| | 密钥认证 | SSH 密钥登录（可选密码） | `server.key`, `server.key_password` |
| | SSH 跳板机 | 通过一台命名的堡垒机访问目标机 | `server.jump_host` |
| | HTTP CONNECT / SOCKS5 代理 | 使用指定代理，失败不回退直连 | `config.yaml` proxy / `server.proxy` |
| | 连接池 | 自动复用，60s 保活 | 全局 `pool.get(name)` |
| **命令执行** | `run()` | 执行命令，可选 sudo，实时流式输出 | `ssh_run` / CLI `run -s` |
| | `run_alias()` | 执行预定义别名（流式输出） | `ssh_alias.server.name` / CLI `alias` |
| | 命令模板 | 包装所有命令（如自动 cd） | `server.command_template` |
| | 命令过滤 | 正则表达式白名单/黑名单 | `server.blacklist` / `server.whitelist` |
| **脚本管理** | `upload_script()` | 上传脚本 | CLI `upload` |
| | `run_script()` | 执行已上传脚本 | `ssh_run_script` / CLI `run-script` |
| | `upload_all_scripts()` | 上传所有别名定义的脚本 | CLI `upload-all` |
| | `list_scripts()` | 列出远程已上传脚本 | CLI `list-scripts` |
| | 上传后执行 | `upload_script(run_immediately=True)` | `ssh_upload_script` / CLI `upload -r` |
| **文件传输** | `download()` | 下载单个文件或递归目录 | `ssh_download` / CLI `download` |
| | 下载过滤 | 正则表达式过滤文件名 | `pattern` 参数 |
| | 下载计数 | 返回目录下载的文件数 | `count` 字段 |
| | 下载 sudo | 读取 root 拥有的文件（通过 /tmp 暂存） | `sudo: true` 参数 |
| | 下载覆盖控制 | 跳过已存在的本地文件 | `overwrite` 参数（默认：true） |
| | 上传覆盖控制 | 跳过已存在的远程脚本 | `overwrite` 参数（默认：true） |
| | 路径限制 | 限制上传/下载路径 | `server.allowed_local_paths` / `server.allowed_remote_paths` |
| **别名系统** | 内联别名 | 直接执行命令字符串 | `aliases[].inline` |
| | 脚本别名 | 上传 + 执行脚本文件 | `aliases[].script` |
| | Sudo 别名 | 以 root 权限执行 | `aliases[].sudo: true` |
| | 继承 | 通过 `extends` 共享别名 | `extends: [_shared/common.yml]` |
| **MCP 工具** | 动态工具 | 每个别名自动暴露为 `ssh_alias.server.name` | 运行时自动生成 |
| | 配置界面 | 管理多台服务器，不读取已保存的密码 | `ssh_open_config` |
| | 工具发现 | `ssh_list_servers` / `ssh_list_aliases` | 静态工具 |
| | 读写标记 | 工具标记 readOnly/destructive | 自动设置 |
| **安全** | 命令黑名单 | 正则表达式阻止命令 | `server.blacklist` |
| | 命令白名单 | 仅允许匹配的命令 | `server.whitelist` |
| | 路径限制 | 限制上传/下载路径 | `server.allowed_local_paths` / `server.allowed_remote_paths` |
| | 代理错误日志 | 代理失败记录到文件 | `proxy_error.log` |
