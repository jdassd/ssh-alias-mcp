#!/usr/bin/env python3
"""SSH Client - Core SSH connection module

Supports direct connection, SSH jump hosts, SOCKS5 proxy with auto-fallback,
connection pooling, and script upload/run.

Usage:

    # Direct Python import
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent))
    from ssh_client import pool

    conn = pool.get("my-server")
    result = conn.run("ls -la /opt")
    print(result["stdout"])

    # MCP mode (via mcp_server.py)
    claude mcp add ssh-alias-mcp python <path>/mcp_server.py
"""
import hashlib
import io
import os
import re
import sys
import time
import threading
import uuid
from pathlib import Path
from typing import Dict, Optional, Any

import paramiko
import yaml

# Force UTF-8 output on Windows
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

SCRIPT_DIR = Path(__file__).parent.resolve()
CONFIG_PATH = SCRIPT_DIR / "config.yaml"


def _normalize_path(raw: str) -> str:
    """Normalize a path string for the current OS.

    Converts Windows-style drive paths (e.g. 'D:\\agents\\servers') to WSL
    /mnt/<drive>/... form when running under Linux. No-op elsewhere.
    """
    if not raw:
        return raw
    s = str(raw).replace("\\", "/")
    
    # Convert Windows path to WSL path when running under Linux
    if sys.platform == "linux":
        # Match "X:/..." pattern
        if len(s) >= 2 and s[1] == ":" and s[0].isalpha():
            drive = s[0].lower()
            rest = s[2:].lstrip("/")
            return f"/mnt/{drive}/{rest}"
        return s
    
    # Convert WSL path to Windows path when running under Windows
    if sys.platform == "win32":
        # Match "/mnt/X/..." pattern
        if s.startswith("/mnt/"):
            parts = s.split("/", 3)
            if len(parts) >= 4:
                drive = parts[2].upper()
                rest = parts[3]
                return f"{drive}:/{rest}"
        return s
    
    return s


def load_yaml(path: str) -> dict:
    """Load a YAML config file."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Config file not found: {p}")
    text = p.read_text(encoding="utf-8-sig")
    return yaml.safe_load(text) or {}


def load_global_config() -> dict:
    """Load global configuration (proxy, timeout, etc.)"""
    if not CONFIG_PATH.exists():
        return {"proxy": {"enabled": False}, "server": {"timeout": 30}}
    return load_yaml(str(CONFIG_PATH))


global_config: dict = load_global_config()

# Codex may map its stable plugin data directory into
# SSH_ALIAS_MCP_DATA_DIR. Some launchers pass the placeholder through
# literally, so fall back to a stable Codex-owned directory in that case.
# Direct source/CLI usage keeps the existing config.yaml behavior.
_plugin_data_dir_cfg = _normalize_path(os.environ.get("SSH_ALIAS_MCP_DATA_DIR", "").strip())
if _plugin_data_dir_cfg and "${" not in _plugin_data_dir_cfg:
    _plugin_data_dir = Path(os.path.expandvars(os.path.expanduser(_plugin_data_dir_cfg)))
    SERVERS_DIR = (_plugin_data_dir / "servers").resolve()
elif _plugin_data_dir_cfg:
    _codex_home_cfg = _normalize_path(os.environ.get("CODEX_HOME", "").strip())
    _codex_home = (
        Path(os.path.expandvars(os.path.expanduser(_codex_home_cfg))).resolve()
        if _codex_home_cfg
        else (Path.home() / ".codex").resolve()
    )
    SERVERS_DIR = (_codex_home / "ssh-alias-mcp" / "servers").resolve()
else:
    # servers_dir can be configured in config.yaml (absolute or relative to config file)
    _servers_dir_cfg = _normalize_path(global_config.get("servers_dir", "servers"))
    SERVERS_DIR = (
        (CONFIG_PATH.parent / _servers_dir_cfg).resolve()
        if _servers_dir_cfg
        else SCRIPT_DIR / "servers"
    )


# Command templates keyed by shell type.
# Available placeholders: path, src, dst, tmp, target, mode, user
CMD_TEMPLATES = {
    "bash": {
        "mkdir":        "mkdir -p {path}",
        "file_exists":  "test -f {path}",
        "run_script":   "bash {path}",
        "chmod":        "chmod {mode:o} {path}",
        "stat_owner":   "stat -c '%U:%G' {path}",
        "stat_mode":    "stat -c '%a' {path}",
        "rm_dir":       "rm -rf {path}",
        "cp_r":         "cp -r {src} {dst}",
        "move":         "mv {tmp} {target}",
        "chown":        "chown {user} {path}",
        "chown_r":      "chown -R {user}:{user} {path}",
        "list_dir":     "ls -la {path}/",
        "install":      (
            "if [ -e '{target}' ]; then "
            "  OWN=$(stat -c '%u:%g' '{target}'); "
            "  MODE=$(stat -c '%a' '{target}'); "
            "  mv {tmp} '{target}' && "
            "  chown $OWN '{target}' && "
            "  chmod $MODE '{target}'; "
            "else "
            "  PARENT=$(dirname '{target}'); "
            "  OWN=$(stat -c '%u:%g' \"$PARENT\"); "
            "  mv {tmp} '{target}' && "
            "  chown $OWN '{target}' && "
            "  chmod 755 '{target}'; "
            "fi"
        ),
        "tmp_prefix":   "/tmp",
    },
    "cmd": {
        "mkdir":        'mkdir "{path}" 2>nul',
        "file_exists":  'if exist "{path}" echo exists',
        "run_script":   'call "{path}"',
        "chmod":        "",  # no-op on Windows cmd
        "stat_owner":   "echo n/a",
        "stat_mode":    "echo n/a",
        "rm_dir":       'rmdir /S /Q "{path}"',
        "cp_r":         'xcopy /Y "{src}" "{dst}" /E /I',
        "move":         'move /y "{tmp}" "{target}"',
        "chown":        "",  # no-op
        "chown_r":      "",  # no-op
        "list_dir":     'dir "{path}" /Q',
        "install":      'move /y "{tmp}" "{target}"',
        "tmp_prefix":   "%TEMP%",
    },
    "powershell": {
        "mkdir":        'New-Item -ItemType Directory -Path "{path}" -Force',
        "file_exists":  'Test-Path "{path}"',
        "run_script":   '& "{path}"',
        "chmod":        "",  # no-op
        "stat_owner":   'echo n/a',
        "stat_mode":    'echo n/a',
        "rm_dir":       'Remove-Item -Recurse -Force "{path}"',
        "cp_r":         'Copy-Item -Recurse "{src}" "{dst}"',
        "move":         'Move-Item -Force "{tmp}" "{target}"',
        "chown":        "",  # no-op
        "chown_r":      "",  # no-op
        "list_dir":     'Get-ChildItem -Path "{path}" | Format-List',
        "install":      'Move-Item -Force "{tmp}" "{target}"',
        "tmp_prefix":   "$env:TEMP",
    },
}


class SSHConnection:
    """SSH connection wrapper with direct, proxy, and jump-host transports."""

    def __init__(self, cfg: dict, aliases: list = None,
                 yml_path: str = None, connection_pool=None):
        self.host = cfg["host"]
        self.port = int(cfg.get("port", 22))
        self.user = cfg["user"]
        self.password = cfg.get("password")
        self.key_path = _normalize_path(cfg.get("key", "")) or None
        self.key_password = cfg.get("key_password")
        self.sudo_password = cfg.get("sudo_password") or cfg.get("password")
        self.timeout = int(cfg.get("timeout",
                                   global_config.get("server", {}).get("timeout", 30)))
        server_defaults = global_config.get("server", {})
        self.auto_reconnect = bool(
            cfg.get("auto_reconnect", server_defaults.get("auto_reconnect", True))
        )
        self.reconnect_interval = max(
            0.0,
            float(cfg.get(
                "reconnect_interval",
                server_defaults.get("reconnect_interval", 5),
            )),
        )
        self.reconnect_fast_attempts = 5
        self.reconnect_slow_interval = 60.0
        self.scripts_dir = cfg.get("scripts_dir",
                                     f"/home/{cfg.get('user', 'root')}/scripts")
        self.scripts_local_dir = cfg.get("scripts_local_dir")
        self.shell = cfg.get("shell", "bash")  # "bash", "cmd", "powershell"
        self._tpl = CMD_TEMPLATES[self.shell]
        self.aliases = aliases or []
        self._yml_path = yml_path
        self.jump_host = cfg.get("jump_host")
        self._connection_pool = connection_pool

        # Security: command filtering
        self.whitelist = [re.compile(p) for p in cfg.get("whitelist", [])]
        self.blacklist = [re.compile(p) for p in cfg.get("blacklist", [])]
        self.command_template = cfg.get("command_template", "")

        # Security: path restrictions for upload/download
        self._allowed_paths = {
            "local": [str(Path(p).resolve()) for p in cfg.get("allowed_local_paths", [])],
            "remote": [str(p) for p in cfg.get("allowed_remote_paths", [])],
        }

        # Proxy config: per-server takes priority, then global fallback
        self.proxy = cfg.get("proxy")
        if not self.proxy:
            proxy_cfg = global_config.get("proxy", {})
            if proxy_cfg.get("enabled"):
                self.proxy = {
                    "host": proxy_cfg.get("host", "127.0.0.1"),
                    "port": proxy_cfg.get("port", 1080)
                }

        self._client: Optional[paramiko.SSHClient] = None
        self._last_used = 0
        self._connect_lock = threading.Lock()
        self._connect_failures = 0
        self._reconnect_wakeup = threading.Event()
        self._reconnect_thread: Optional[threading.Thread] = None
        self._closed = False
        self._jump_connection = None
        self._jump_channel = None

    def connect(self):
        """Connect for an explicit request, bypassing any reconnect cooldown."""
        with self._connect_lock:
            self._closed = False
            if self._client and self._is_alive():
                self._last_used = time.time()
                return
            try:
                self._connect_once()
            except Exception:
                self._record_connect_failure()
                raise
            self._connect_failures = 0
            self._reconnect_wakeup.set()

    def _connect_once(self):
        """Perform one configured connection cycle."""
        self._close_target_connection()

        # A jump host is an explicit routing requirement. Never bypass it by
        # falling back to a direct connection to the target.
        if self.jump_host:
            self._connect_via_jump()
            return

        # Dynamically reload proxy config
        current_proxy_cfg = load_global_config().get("proxy", {})
        if current_proxy_cfg.get("enabled"):
            proxy = {
                "host": current_proxy_cfg.get("host", "127.0.0.1"),
                "port": current_proxy_cfg.get("port", 1080)
            }
            if self.proxy:
                proxy = self.proxy
        elif self.proxy:
            proxy = self.proxy
        else:
            proxy = None

        # Check pysocks availability
        if proxy:
            import socks  # noqa: F401

        # Try proxy connection first
        if proxy:
            try:
                self._connect_proxy_with(proxy)
                return
            except Exception as e:
                error_msg = f"Proxy connection failed ({type(e).__name__}: {e}), fallback to direct..."
                print(error_msg, file=sys.stderr)
                self._log_proxy_error(proxy, e)

        # Direct connection (fallback after proxy failure)
        self._connect_direct()

    def _record_connect_failure(self):
        """Record a failed cycle and ensure policy-controlled background retries."""
        self._connect_failures += 1
        if not self.auto_reconnect or self._closed:
            return
        if not self._reconnect_thread or not self._reconnect_thread.is_alive():
            self._reconnect_thread = threading.Thread(
                target=self._reconnect_loop,
                name=f"ssh-reconnect-{self.host}",
                daemon=True,
            )
            self._reconnect_thread.start()
        self._reconnect_wakeup.set()

    def _reconnect_loop(self):
        """Retry quickly for five failures, then once per minute."""
        while True:
            with self._connect_lock:
                if (
                    self._closed
                    or not self.auto_reconnect
                    or (self._client and self._is_alive())
                ):
                    return
                delay = (
                    self.reconnect_interval
                    if self._connect_failures < self.reconnect_fast_attempts
                    else self.reconnect_slow_interval
                )
                self._reconnect_wakeup.clear()

            # Explicit requests set this event after their immediate attempt,
            # restarting the policy delay without causing a duplicate attempt.
            if self._reconnect_wakeup.wait(delay):
                continue

            with self._connect_lock:
                if (
                    self._closed
                    or not self.auto_reconnect
                    or (self._client and self._is_alive())
                ):
                    return
                try:
                    self._connect_once()
                except Exception as exc:
                    self._connect_failures += 1
                    phase = (
                        "fast"
                        if self._connect_failures < self.reconnect_fast_attempts
                        else "60s cooldown"
                    )
                    print(
                        f"SSH reconnect failed "
                        f"(consecutive failures={self._connect_failures}, next={phase}): "
                        f"{type(exc).__name__}: {exc}",
                        file=sys.stderr,
                    )
                    continue
                self._connect_failures = 0
                return

    def _close_target_connection(self):
        """Close the target client and its forwarding channel, not the shared jump host."""
        client = self._client
        channel = self._jump_channel
        self._client = None
        self._jump_channel = None
        self._jump_connection = None

        if client is not None:
            try:
                client.close()
            except Exception:
                pass
        if channel is not None:
            try:
                channel.close()
            except Exception:
                pass

    def _connect_via_jump(self):
        """Connect to the target through one pooled SSH jump host."""
        if self._connection_pool is None:
            raise RuntimeError(
                "jump_host connections must be created by ConnectionPool"
            )

        jump = self._connection_pool.get(self.jump_host)
        jump.connect()
        jump_transport = (
            jump._client.get_transport()
            if jump._client is not None
            else None
        )
        if jump_transport is None or not jump_transport.is_active():
            raise paramiko.SSHException(
                f"Jump host connection is not active: {self.jump_host}"
            )

        channel = None
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            channel = jump_transport.open_channel(
                "direct-tcpip",
                dest_addr=(self.host, self.port),
                src_addr=("127.0.0.1", 0),
                timeout=self.timeout,
            )
            channel.settimeout(self.timeout)
            client.connect(
                hostname=self.host,
                port=self.port,
                username=self.user,
                timeout=self.timeout,
                sock=channel,
                **self._get_auth(),
            )
            target_transport = client.get_transport()
            if target_transport is None:
                raise paramiko.SSHException("Target SSH transport was not created")
            # The timeout protects target handshake/authentication. Restore the
            # forwarding channel to blocking mode for a long-lived SSH transport.
            channel.settimeout(None)
            target_transport.set_keepalive(60)
            self._jump_connection = jump
            self._jump_channel = channel
            self._client = client
            self._last_used = time.time()
        except Exception:
            client.close()
            if channel is not None:
                channel.close()
            raise

    def _connect_proxy_with(self, proxy: dict):
        """Connect via SOCKS5 proxy"""
        import socks
        sock = socks.socksocket()
        transport = None
        try:
            sock.set_proxy(socks.SOCKS5, proxy["host"], int(proxy["port"]))
            sock.settimeout(self.timeout)
            sock.connect((self.host, self.port))

            c = paramiko.SSHClient()
            c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            transport = paramiko.Transport(sock)
            kw = self._get_auth()
            transport.connect(username=self.user, **kw)
            transport.set_keepalive(60)
            c._transport = transport
            self._client = c
            self._last_used = time.time()
        except Exception:
            if transport is not None:
                transport.close()
            else:
                sock.close()
            raise

    def _connect_direct(self):
        """Connect directly (no proxy)"""
        c = paramiko.SSHClient()
        c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kw = self._get_auth()
        try:
            c.connect(
                self.host,
                port=self.port,
                username=self.user,
                timeout=self.timeout,
                **kw,
            )
            c.get_transport().set_keepalive(60)
            self._client = c
            self._last_used = time.time()
        except Exception:
            c.close()
            raise

    def _get_auth(self) -> dict:
        """Get authentication parameters (key or password)"""
        if self.key_path:
            kw = {"key_filename": self.key_path}
            if self.key_password:
                kw["passphrase"] = self.key_password
            return kw
        return {"password": self.password}

    def _is_alive(self) -> bool:
        try:
            return bool(self._client and self._client.get_transport().is_active())
        except Exception:
            return False

    def _log_proxy_error(self, proxy: dict, exc: Exception):
        """Log proxy connection errors to a log file"""
        log_path = SCRIPT_DIR / "proxy_error.log"
        try:
            import traceback
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {exc}\n")
                f.write(f"  Target: {self.user}@{self.host}:{self.port}\n")
                f.write(f"  Proxy: {proxy}\n")
                f.write(traceback.format_exc())
                f.write("---\n")
        except Exception:
            pass

    def download(self, remote_path: str, local_path: str, timeout: int = 300,
                 pattern: str = None, overwrite: bool = True, sudo: bool = False) -> dict:
        """Download a file or directory from remote to local via SFTP.

        If remote_path is a directory, recursively downloads all files.
        Optionally filter by regex pattern on filenames.

        Args:
            sudo: if True, read root-owned files by staging through /tmp:
                  `sudo cp -r <src> /tmp/<uuid>` -> `sudo chown <user>` -> SFTP get -> remove tmp.
        """
        self.connect()

        if self._path_allowed(local_path, "local"):
            raise ValueError(f"Local path not allowed: {local_path}")

        # When sudo, stage entire source tree to a user-readable temp location first
        actual_remote = remote_path
        tmp_stage = None
        if sudo:
            tmp_prefix = self._cmd("tmp_prefix")
            tmp_stage = f"{tmp_prefix}/.dl_stage_{uuid.uuid4().hex[:8]}"
            stage = self.run(
                self._cmd("cp_r", src=remote_path, dst=tmp_stage),
                timeout=timeout, sudo=True,
            )
            if stage["code"] != 0:
                raise RuntimeError(f"sudo stage failed: {stage['stderr'].strip()[:200]}")
            if self._is_unix():
                self.run(self._cmd("chown_r", user=self.user, path=tmp_stage),
                          timeout=10, sudo=True)
            actual_remote = tmp_stage

        try:
            # Check if (staged) remote is a directory
            sftp = paramiko.SFTPClient.from_transport(self._client.get_transport())
            remote_attr = sftp.stat(actual_remote)
            is_dir = (remote_attr.st_mode & 0o170000) == 0o040000

            if not is_dir:
                if self._path_allowed(remote_path, "remote"):
                    raise ValueError(f"Remote path not allowed: {remote_path}")
                local_path_obj = Path(local_path)
                if not overwrite and local_path_obj.exists():
                    raise FileExistsError(f"Local path already exists: {local_path}")
                local_dir = local_path_obj.parent
                if not local_dir.exists():
                    local_dir.mkdir(parents=True, exist_ok=True)
                sftp.get(actual_remote, str(local_path))
                sftp.close()
                return {
                    "stdout": f"Downloaded {remote_path} -> {local_path}\n",
                    "stderr": "",
                    "code": 0,
                    "remote_path": remote_path,
                    "local_path": local_path,
                }

            # Directory download
            return self._download_dir(sftp, actual_remote, local_path, pattern, timeout,
                                      display_remote=remote_path)
        finally:
            if tmp_stage:
                self.run(self._cmd("rm_dir", path=tmp_stage), timeout=15, sudo=True)

    def _download_dir(self, sftp, remote_dir: str, local_dir: str,
                      pattern: str = None, timeout: int = 300,
                      display_remote: str = None) -> dict:
        """Recursively download a remote directory."""
        if pattern:
            pat = re.compile(pattern)
        display = display_remote or remote_dir

        total = 0
        errors = []

        def recurse(remote, local):
            nonlocal total
            if not local.exists():
                local.mkdir(parents=True, exist_ok=True)
            entries = sftp.listdir_attr(remote)
            for entry in entries:
                name = entry.filename
                if name in (".", ".."):
                    continue
                rpath = f"{remote}/{name}"
                lpath = local / name
                is_dir = (entry.st_mode & 0o170000) == 0o040000
                if is_dir:
                    recurse(rpath, lpath)
                else:
                    if pattern and not pat.search(name):
                        continue
                    if self._path_allowed(rpath, "remote"):
                        errors.append(f"  x {rpath}: path not allowed")
                        continue
                    lpath.parent.mkdir(parents=True, exist_ok=True)
                    sftp.get(rpath, str(lpath))
                    total += 1

        local_path = Path(local_dir)
        if not local_path.exists():
            local_path.mkdir(parents=True, exist_ok=True)
        recurse(remote_dir, local_path)
        sftp.close()

        lines = [f"Downloaded {total} files from {display} -> {local_dir}\n"]
        if errors:
            lines.append("Errors:\n" + "\n".join(errors))
        return {
            "stdout": "".join(lines),
            "stderr": "",
            "code": 1 if errors else 0,
            "remote_path": display,
            "local_path": str(local_dir),
            "count": total,
        }

    def _path_allowed(self, path: str, kind: str) -> bool:
        """Check if path is in allowed list. Returns True if disallowed."""
        allowed = self._allowed_paths.get(kind, [])
        if not allowed:
            return False  # No restriction
        resolved = str(Path(path).resolve())
        return not any(resolved.startswith(p) for p in allowed)

    def _check_command(self, cmd: str) -> str:
        """Check command against whitelist/blacklist, return template if set. Raises on violation."""
        # Check blacklist
        for pattern in self.blacklist:
            if pattern.search(cmd):
                raise ValueError(f"Command blocked by blacklist: {pattern.pattern}")
        # Check whitelist (if configured)
        if self.whitelist:
            if not any(p.search(cmd) for p in self.whitelist):
                raise ValueError(f"Command not in whitelist: {cmd}")
        # Apply command template
        if self.command_template:
            cmd = self.command_template.replace("<command>", cmd)
        return cmd

    def _wrap_sudo(self, cmd: str) -> str:
        """Wrap a command to run via sudo.

        bash: uses echo|sudo -S to avoid TTY prompt.
        cmd/powershell: uses echo|sudo -S bash -c (requires bash available on remote).
        Raises ValueError for non-bash shells if sudo_password is not set.
        """
        if not self.sudo_password:
            raise ValueError("sudo_password not configured for this server")
        if self.shell == "bash":
            safe = cmd.replace("'", "'\\''")
            return f"echo '{self.sudo_password}' | sudo -S bash -c '{safe}'"
        elif self.shell in ("cmd", "powershell"):
            # Windows: sudo requires bash available (e.g., WSL or Git Bash)
            safe = cmd.replace("'", "'\\''")
            return f"echo '{self.sudo_password}' | sudo -S bash -c '{safe}'"
        raise ValueError(f"Unsupported shell for sudo: {self.shell}")

    def _strip_sudo_noise(self, result: dict) -> dict:
        """Remove '[sudo]' password prompt lines from output."""
        for stream in ("stdout", "stderr"):
            result[stream] = "\n".join(
                l for l in result[stream].split("\n")
                if "Password:" not in l and "[sudo:" not in l
            ).strip("\n")
        return result

    def run(self, cmd: str, timeout: int = None, sudo: bool = False, stream_cb: Any = None) -> dict:
        """Execute a remote command, returns {stdout, stderr, code}.

        Args:
            cmd: shell command to execute
            timeout: seconds before giving up (defaults to server timeout from YAML)
            sudo: if True, wrap with `echo pwd | sudo -S bash -c '...'`
            stream_cb: optional callback(chunk: str, is_stderr: bool) for real-time output
        """
        if timeout is None:
            timeout = self.timeout
        cmd = self._check_command(cmd)
        if sudo:
            cmd = self._wrap_sudo(cmd)
        self.connect()
        ch = self._client.get_transport().open_session()
        ch.settimeout(timeout)
        ch.exec_command(cmd)

        stdout_data, stderr_data = [], []
        deadline = time.time() + timeout
        while time.time() < deadline:
            if ch.recv_ready():
                chunk = ch.recv(65536)
                stdout_data.append(chunk)
                if stream_cb:
                    stream_cb(chunk.decode(errors="replace"), False)
            if ch.recv_stderr_ready():
                chunk = ch.recv_stderr(65536)
                stderr_data.append(chunk)
                if stream_cb:
                    stream_cb(chunk.decode(errors="replace"), True)
            if ch.exit_status_ready() and not ch.recv_ready() and not ch.recv_stderr_ready():
                break
            time.sleep(0.05)

        while ch.recv_ready():
            chunk = ch.recv(65536)
            stdout_data.append(chunk)
            if stream_cb:
                stream_cb(chunk.decode(errors="replace"), False)
        while ch.recv_stderr_ready():
            chunk = ch.recv_stderr(65536)
            stderr_data.append(chunk)
            if stream_cb:
                stream_cb(chunk.decode(errors="replace"), True)

        rc = ch.recv_exit_status() if ch.exit_status_ready() else -1
        ch.close()
        result = {
            "stdout": b"".join(stdout_data).decode(errors="replace"),
            "stderr": b"".join(stderr_data).decode(errors="replace"),
            "code": rc,
        }
        return self._strip_sudo_noise(result) if sudo else result

    def upload_script(self, local_path: str, script_name: str = None,
                      run_immediately: bool = False, timeout: int = 300,
                      overwrite: bool = True, sudo: bool = False) -> dict:
        """Upload a local script to the remote scripts_dir, optionally run immediately.

        Args:
            sudo: if True, write the script as root via stage-via-/tmp
                  (SFTP -> tmp -> sudo mv -> chmod). When run_immediately=True,
                  also execute the script as root.
        """
        self.connect()

        if not Path(local_path).exists():
            raise FileNotFoundError(f"Local script not found: {local_path}")
        script_content = Path(local_path).read_bytes()

        if not script_name:
            script_name = Path(local_path).name
        remote_path = f"{self.scripts_dir}/{script_name}"

        if not overwrite:
            check = self.run(self._cmd("file_exists", path=remote_path), timeout=5, sudo=sudo)
            if check["code"] == 0:
                raise FileExistsError(f"Remote script already exists: {remote_path}")

        self.run(self._cmd("mkdir", path=self.scripts_dir), timeout=10, sudo=sudo)

        if sudo:
            tmp_prefix = self._cmd("tmp_prefix")
            tmp_path = f"{tmp_prefix}/.staging_{uuid.uuid4().hex[:8]}_{script_name}"
            sftp = paramiko.SFTPClient.from_transport(self._client.get_transport())
            sftp.putfo(io.BytesIO(script_content), tmp_path)
            sftp.close()
            self.run(self._cmd("install", tmp=tmp_path, target=remote_path), timeout=15, sudo=True)
        else:
            sftp = paramiko.SFTPClient.from_transport(self._client.get_transport())
            sftp.putfo(io.BytesIO(script_content), remote_path)
            if self._is_unix():
                sftp.chmod(remote_path, 0o755)
            sftp.close()

        result = {
            "stdout": f"Script uploaded to {remote_path}\n",
            "stderr": "",
            "code": 0,
            "remote_path": remote_path
        }

        if run_immediately:
            run_cmd = self._cmd("run_script", path=remote_path)
            run_result = self.run(run_cmd, timeout=timeout, sudo=sudo)
            result["run_stdout"] = run_result["stdout"]
            result["run_stderr"] = run_result["stderr"]
            result["run_code"] = run_result["code"]
            result["code"] = run_result["code"]

        return result

    def list_scripts(self, sudo: bool = False) -> dict:
        """List uploaded scripts on the remote server"""
        self.connect()
        return self.run(self._cmd("list_dir", path=self.scripts_dir), timeout=30, sudo=sudo)

    def run_script(self, script_name: str, timeout: int = 300, sudo: bool = False, stream_cb: Any = None) -> dict:
        """Run an already-uploaded script.

        Args:
            script_name: file name under scripts_dir
            timeout: command timeout in seconds
            sudo: if True, execute as root (requires sudo_password configured)
            stream_cb: optional callback(chunk: str, is_stderr: bool) for real-time output
        """
        self.connect()
        remote_path = f"{self.scripts_dir}/{script_name}"
        return self.run(self._cmd("run_script", path=remote_path), timeout=timeout, sudo=sudo, stream_cb=stream_cb)

    def _cmd(self, name: str, **kwargs) -> str:
        """Format a command template with the given parameters."""
        tpl = self._tpl[name]
        if not tpl:
            return ""
        return tpl.format(**kwargs)

    def _scripts_base(self) -> Path:
        """Get the base directory for resolving relative script paths.
        Resolution:
          1. If scripts_local_dir is set, use {yml_dir}/{scripts_local_dir}
          2. Otherwise fall back to yml file's directory.
        """
        base = Path(self._yml_path).parent.resolve() if self._yml_path else Path.cwd()
        if self.scripts_local_dir:
            return (base / self.scripts_local_dir).resolve()
        return base

    def _is_unix(self) -> bool:
        """Check if shell is Unix-like."""
        return self.shell == "bash"

    def upload_all_scripts(self, sudo: bool = False, overwrite: bool = True) -> dict:
        """Upload all scripts referenced by alias 'script' fields.

        Args:
            sudo: if True, uploads via stage-via-/tmp + sudo mv (for root-owned dirs).
            overwrite: if False, skip files that already exist on remote (default True).
        """
        script_aliases = [a for a in self.aliases if a.get("script")]
        if not script_aliases:
            return {"stdout": "No alias with 'script' field found.\n", "stderr": "", "code": 0}

        self.connect()
        self.run(self._cmd("mkdir", path=self.scripts_dir), timeout=10, sudo=sudo)

        sftp = paramiko.SFTPClient.from_transport(self._client.get_transport())
        msg, fail = [], []
        seen_hashes = {}
        base = self._scripts_base()
        for a in script_aliases:
            src = (base / a["script"]).resolve()
            if not src.is_file():
                fail.append(f"  x {a['script']}: file not found at {src}")
                continue
            content_hash = hashlib.md5(src.read_bytes()).hexdigest()
            if content_hash in seen_hashes:
                prev_alias = seen_hashes[content_hash]
                fail.append(f"  x {a['script']}: duplicate content of {prev_alias}")
                continue
            seen_hashes[content_hash] = a["name"]
            remote_name = src.name
            if src.is_relative_to(base):
                rel = src.relative_to(base).as_posix()
                remote = f"{self.scripts_dir}/{rel}"
            else:
                remote = f"{self.scripts_dir}/external/{remote_name}"
            remote_parent = str(Path(remote).parent)
            self.run(self._cmd("mkdir", path=remote_parent), timeout=10, sudo=sudo)
            self._upload_single(sftp, src, remote, sudo, a, msg, fail, overwrite)
        sftp.close()

        out = f"Upload done ({len(msg)}/{len(script_aliases)})\n"
        out += "\n".join(msg + fail) + "\n"
        return {"stdout": out, "stderr": "", "code": 0 if not fail else 1}

    def _upload_single(self, sftp, src: Path, remote: str, sudo: bool,
                        alias: dict, msg: list, fail: list, overwrite: bool = True):
        """Upload a single script file via SFTP.

        Args:
            overwrite: if False, skip files that already exist on remote.
        """
        try:
            # Check if file exists and overwrite is False
            if not overwrite:
                try:
                    sftp.stat(remote)
                    msg.append(f"  = {remote} (skipped, already exists)")
                    return
                except Exception:
                    pass  # File doesn't exist, proceed with upload
            remote_dir = str(Path(remote).parent)
            # Handle Windows paths (C:/...) and Unix paths (/...)
            if "/" in remote_dir:
                parts = remote_dir.split("/")
                # Preserve drive letter if present (e.g. C:)
                start = 1 if parts and len(parts[0]) == 2 and parts[0][1] == ":" else 0
                for i in range(start + 1, len(parts) + 1):
                    p = "/".join(parts[:i])
                    try:
                        sftp.mkdir(p)
                    except Exception:
                        pass
            if sudo:
                tmp_prefix = self._cmd("tmp_prefix")
                tmp_path = f"{tmp_prefix}/.staging_{uuid.uuid4().hex[:8]}_{Path(remote).name}"
                sftp.put(str(src), tmp_path)
                mv = self.run(self._cmd("install", tmp=tmp_path, target=remote),
                              timeout=15, sudo=True)
                if mv["code"] != 0:
                    fail.append(f"  x {alias['script']}: sudo install failed: {mv['stderr'][:80]}")
                    return
            else:
                sftp.put(str(src), remote)
                if self._is_unix():
                    sftp.chmod(remote, 0o755)
            msg.append(f"  + {remote}")
        except Exception as e:
            fail.append(f"  x {alias['script']}: {e}")

    def run_alias(self, name: str, stream_cb: Any = None) -> dict:
        """Run an alias-defined quick command"""
        target = None
        for a in self.aliases:
            if a.get("name") == name:
                target = a
                break
        if not target:
            raise ValueError(f"Alias not found: {name}")
        timeout = int(target.get("timeout", 300))
        sudo = target.get("sudo", False)

        # inline type: execute command directly on remote
        if "inline" in target:
            return self.run(target["inline"], timeout=timeout, sudo=sudo, stream_cb=stream_cb)

        # script type: upload local .sh first, then execute remotely
        local_file = (self._scripts_base() / target["script"]).resolve()
        if not local_file.is_file():
            raise FileNotFoundError(f"Script not found: {local_file}")
        if local_file.is_relative_to(self._scripts_base()):
            rel = local_file.relative_to(self._scripts_base()).as_posix()
            remote_path = f"{self.scripts_dir}/{rel}"
        else:
            remote_path = f"{self.scripts_dir}/external/{local_file.name}"

        # Check if already uploaded (skip re-upload). Use sudo=sudo so we can stat
        # files owned by root when the alias targets a root-owned scripts_dir.
        result = self.run(self._cmd("file_exists", path=remote_path), timeout=5, sudo=sudo)
        if result["code"] != 0:
            self.upload_script(str(local_file), script_name=local_file.name,
                               timeout=30, sudo=sudo)

        return self.run(self._cmd("run_script", path=remote_path), timeout=timeout, sudo=sudo, stream_cb=stream_cb)

    def list_aliases(self) -> list:
        """List all configured aliases"""
        aliases = []
        for a in self.aliases:
            item = {
                "name": a.get("name", ""),
                "type": "script" if "script" in a else "inline",
                "desc": a.get("desc", ""),
                "timeout": a.get("timeout", 300),
                "sudo": a.get("sudo", False),
            }
            if "inline" in a:
                item["inline"] = a["inline"]
            if "script" in a:
                item["script"] = a["script"]
            aliases.append(item)
        return aliases

    def close(self):
        """Close the SSH connection"""
        with self._connect_lock:
            self._closed = True
            self._reconnect_wakeup.set()
            self._close_target_connection()


class ConnectionPool:
    """SSH connection pool — manages connection reuse by server name"""
    _lock = threading.Lock()

    def __init__(self):
        self._pool: Dict[str, SSHConnection] = {}

    def get(self, server_name: str) -> SSHConnection:
        """Get or create an SSHConnection (lazy — call conn.connect() on first use)."""
        key = self._to_key(server_name)
        yml_path = self._resolve_config_path(key)
        data = self._load_config(key)
        self._validate_server_config(data)
        cfg = dict(data["server"])
        jump_host = self._resolve_jump_host(key, cfg)
        if jump_host:
            cfg["jump_host"] = jump_host
        aliases = data.get("aliases", [])
        with self._lock:
            if key in self._pool:
                conn = self._pool[key]
                return conn
            conn = SSHConnection(
                cfg,
                aliases,
                yml_path,
                connection_pool=self,
            )
            self._pool[key] = conn
            return conn

    def get_server_config(self, server_name: str) -> dict:
        """Return editable server fields without exposing stored secrets."""
        key, config_path = self._config_target(server_name)
        if not config_path.exists():
            raise FileNotFoundError(f"Server config not found: {config_path}")

        raw = load_yaml(str(config_path))
        resolved = self._load_config(key)
        server = resolved.get("server", {})
        if not isinstance(server, dict):
            server = {}
        editable_fields = (
            "host",
            "port",
            "user",
            "key",
            "name",
            "desc",
            "group",
            "system",
            "timeout",
            "auto_reconnect",
            "reconnect_interval",
            "scripts_dir",
            "scripts_local_dir",
            "shell",
            "jump_host",
        )
        safe_server = {
            field: server[field]
            for field in editable_fields
            if field in server
        }
        return {
            "name": key,
            "server": safe_server,
            "secrets": {
                "password": bool(server.get("password")),
                "key_password": bool(server.get("key_password")),
                "sudo_password": bool(server.get("sudo_password")),
            },
            "inherited": bool(raw.get("extends")),
        }

    def create_server(self, server_name: str, config: dict) -> dict:
        """Create a server YAML file without overwriting an existing config."""
        key, config_path = self._config_target(server_name)
        if config_path.exists():
            raise FileExistsError(f"Server config already exists: {config_path}")
        self._validate_server_config(config)
        self._write_config(config_path, config)
        self._discard(key)
        return {"name": key, "path": str(config_path), "created": True}

    def update_server(self, server_name: str, config: dict, replace: bool = False) -> dict:
        """Recursively merge a server config patch, or replace the whole config."""
        key, config_path = self._config_target(server_name)
        if not config_path.exists():
            raise FileNotFoundError(f"Server config not found: {config_path}")
        current = load_yaml(str(config_path))
        updated = config if replace else self._deep_merge(current, config)
        self._validate_server_config(updated)
        self._write_config(config_path, updated)
        self._discard(key)
        return {
            "name": key,
            "path": str(config_path),
            "updated": True,
            "replace": replace,
        }

    def copy_server(self, source_server: str, target_server: str) -> dict:
        """Copy a server YAML file to a new name without overwriting."""
        source_key, source_path = self._config_target(source_server)
        target_key, target_path = self._config_target(target_server)
        if not source_path.exists():
            raise FileNotFoundError(f"Server config not found: {source_path}")
        if target_path.exists():
            raise FileExistsError(f"Server config already exists: {target_path}")
        config = load_yaml(str(source_path))
        self._validate_server_config(config)
        self._copy_config_file(source_path, target_path)
        self._discard(target_key)
        return {
            "source": source_key,
            "name": target_key,
            "path": str(target_path),
            "copied": True,
        }

    def delete_server(self, server_name: str) -> dict:
        """Delete one server YAML file."""
        key, config_path = self._config_target(server_name)
        if not config_path.exists():
            raise FileNotFoundError(f"Server config not found: {config_path}")
        config_path.unlink()
        self._discard(key)
        return {"name": key, "path": str(config_path), "deleted": True}

    def _config_target(self, server_name: str):
        if not isinstance(server_name, str):
            raise ValueError("Server name must be a string")
        name = server_name.strip()
        if name.lower().endswith((".yml", ".yaml")):
            name = name.rsplit(".", 1)[0]
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
            raise ValueError(
                "Invalid server name; use letters, numbers, dot, underscore, or hyphen"
            )
        key = name.lower()
        return key, SERVERS_DIR / f"{key}.yml"

    def _resolve_jump_host(self, server_name: str, server: dict):
        """Validate and canonicalize a single-hop jump-host reference."""
        raw_jump_host = server.get("jump_host")
        if raw_jump_host is None:
            return None

        jump_key, jump_path = self._config_target(raw_jump_host)
        if jump_key == server_name:
            raise ValueError(
                f"server.jump_host cannot reference itself: {server_name}"
            )
        if not jump_path.exists():
            raise FileNotFoundError(
                f"Jump host config not found: {jump_path}"
            )

        jump_data = self._load_config(jump_key)
        self._validate_server_config(jump_data)
        nested_jump = jump_data["server"].get("jump_host")
        if nested_jump:
            raise ValueError(
                "Only one jump host layer is supported; "
                f"jump host '{jump_key}' also configures jump_host '{nested_jump}'"
            )
        return jump_key

    @staticmethod
    def _validate_server_config(config: dict):
        if not isinstance(config, dict):
            raise ValueError("Server config must be an object")
        server = config.get("server")
        if not isinstance(server, dict):
            raise ValueError("Server config must contain a 'server' object")
        if not config.get("extends"):
            for field in ("host", "user"):
                if not isinstance(server.get(field), str) or not server[field].strip():
                    raise ValueError(
                        f"Server config must contain a non-empty server.{field}"
                    )
        jump_host = server.get("jump_host")
        if jump_host is not None:
            if not isinstance(jump_host, str) or not jump_host.strip():
                raise ValueError("server.jump_host must be a non-empty server name")
            jump_name = jump_host.strip()
            if jump_name.lower().endswith((".yml", ".yaml")):
                jump_name = jump_name.rsplit(".", 1)[0]
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", jump_name):
                raise ValueError(
                    "Invalid server.jump_host; use letters, numbers, dot, "
                    "underscore, or hyphen"
                )
            if server.get("proxy"):
                raise ValueError(
                    "server.proxy cannot be combined with server.jump_host; "
                    "configure the proxy on the jump host instead"
                )
        aliases = config.get("aliases")
        if aliases is not None and not isinstance(aliases, list):
            raise ValueError("Server config 'aliases' must be an array")
        if aliases is not None:
            for index, alias in enumerate(aliases):
                if not isinstance(alias, dict):
                    raise ValueError(f"Alias at index {index} must be an object")
                if not isinstance(alias.get("name"), str) or not alias["name"].strip():
                    raise ValueError(f"Alias at index {index} must have a non-empty name")

    @staticmethod
    def _deep_merge(current: dict, patch: dict) -> dict:
        if not isinstance(patch, dict):
            raise ValueError("Server config patch must be an object")
        merged = dict(current)
        for key, value in patch.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = ConnectionPool._deep_merge(merged[key], value)
            else:
                merged[key] = value
        return merged

    @staticmethod
    def _write_config(config_path: Path, config: dict, mode: int = None):
        config_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = config_path.with_name(f".{config_path.name}.{uuid.uuid4().hex}.tmp")
        try:
            text = yaml.safe_dump(
                config,
                allow_unicode=True,
                sort_keys=False,
                default_flow_style=False,
            )
            temp_path.write_text(text, encoding="utf-8")
            file_mode = mode
            if file_mode is None:
                file_mode = (
                    config_path.stat().st_mode & 0o777
                    if config_path.exists()
                    else 0o600
                )
            temp_path.chmod(file_mode)
            os.replace(temp_path, config_path)
        finally:
            if temp_path.exists():
                temp_path.unlink()

    @staticmethod
    def _copy_config_file(source_path: Path, target_path: Path):
        target_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = target_path.with_name(f".{target_path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temp_path.write_bytes(source_path.read_bytes())
            temp_path.chmod(source_path.stat().st_mode & 0o777)
            os.replace(temp_path, target_path)
        finally:
            if temp_path.exists():
                temp_path.unlink()

    def _discard(self, key: str):
        with self._lock:
            conn = self._pool.pop(key, None)
        if conn is not None:
            conn.close()

    def close_all(self):
        """Close and remove all cached connections."""
        with self._lock:
            connections = list(self._pool.values())
            self._pool.clear()
        for conn in connections:
            conn.close()

    def _resolve_config_path(self, name: str) -> str:
        _, config_path = self._config_target(name)
        if not config_path.exists():
            raise FileNotFoundError(f"Server config not found: {config_path}")
        return str(config_path)

    def _load_config(self, name: str) -> dict:
        config_path = SERVERS_DIR / f"{name}.yml"
        if not config_path.exists():
            raise FileNotFoundError(f"Server config not found: {config_path}")
        cfg = load_yaml(str(config_path))

        # Handle 'extends' inheritance
        extends = cfg.pop("extends", [])
        if extends:
            base_cfg = dict(cfg)
            base_aliases = base_cfg.get("aliases", [])
            for ext_file in extends:
                ext_path = config_path.parent / ext_file
                if not ext_path.exists():
                    print(f"Warning: extends file not found: {ext_path}", file=sys.stderr)
                    continue
                ext_cfg = load_yaml(str(ext_path))
                # Merge server fields: base + local overrides
                ext_server = ext_cfg.get("server", {})
                if ext_server:
                    merged_server = {**ext_server, **base_cfg.get("server", {})}
                    base_cfg["server"] = merged_server
                # Merge aliases: local takes priority
                ext_aliases = ext_cfg.get("aliases", [])
                if ext_aliases:
                    existing_names = {a["name"] for a in base_cfg.get("aliases", [])}
                    base_cfg["aliases"] = [
                        a for a in ext_aliases if a.get("name") not in existing_names
                    ] + base_cfg.get("aliases", [])
                # Merge security fields
                for key in ["whitelist", "blacklist", "command_template",
                            "allowed_local_paths", "allowed_remote_paths", "scripts_dir"]:
                    if key not in base_cfg and key in ext_cfg:
                        base_cfg[key] = ext_cfg[key]
                # Merge proxy config
                if "proxy" in ext_cfg and "proxy" not in base_cfg:
                    base_cfg["proxy"] = ext_cfg["proxy"]
            cfg = base_cfg

        return cfg

    def list_servers(self) -> list:
        """List all configured servers"""
        if not SERVERS_DIR.exists():
            return []
        servers = []
        for f in sorted(SERVERS_DIR.glob("*.yml")):
            try:
                name = f.stem
                cfg = self._load_config(name)
                srv = cfg["server"]
                info = {
                    "name": name,
                    "host": srv["host"],
                    "port": srv.get("port", 22),
                    "user": srv["user"],
                    "display": srv.get("name", name),
                    "desc": srv.get("desc", ""),
                    "system": srv.get("system", ""),
                    "group": srv.get("group", name),
                    "shell": srv.get("shell", "bash"),
                }
                if srv.get("jump_host"):
                    info["jump_host"] = srv["jump_host"]
                aliases = cfg.get("aliases", [])
                if aliases:
                    info["aliases"] = len(aliases)
                servers.append(info)
            except Exception as e:
                servers.append({"name": f.stem, "error": str(e)})
        servers.sort(key=lambda s: (s.get("group", ""), s.get("name", "")))
        for i, srv in enumerate(servers, start=1):
            srv["index"] = i
        return servers

    @staticmethod
    def _to_key(name: str) -> str:
        return name.replace(".yml", "").replace(".yaml", "").strip().lower()


# Global connection pool instance
pool = ConnectionPool()
