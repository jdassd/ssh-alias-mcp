"""Loopback-only browser host for the SSH server configuration page."""
import hashlib
import json
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


CONFIG_TOOLS = frozenset({
    "ssh_list_servers", "ssh_get_server_config", "ssh_ui_bootstrap",
    "ssh_ui_create_server", "ssh_ui_update_server", "ssh_ui_delete_server",
})
WRITE_TOOLS = frozenset({
    "ssh_ui_create_server", "ssh_ui_update_server", "ssh_ui_delete_server",
})
MAX_REQUEST_BYTES = 128 * 1024
CONFIG_IDLE_SECONDS = 1800


class BoundedHTTPServer(ThreadingHTTPServer):
    """Limit concurrent browser requests without making the listener wait."""

    def __init__(self, *args, **kwargs):
        self.request_slots = threading.BoundedSemaphore(16)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self.request_slots.acquire(blocking=False):
            try:
                request.sendall(b"HTTP/1.0 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.request_slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.request_slots.release()


class ConfigWebServer:
    """Reuse configuration snapshots; expire an unused browser host."""

    def __init__(self, ui_path: Path, call_tool, config_revision, idle_seconds=CONFIG_IDLE_SECONDS):
        self.call_tool = call_tool
        self.config_revision = config_revision
        self.idle_seconds = idle_seconds
        self.token = secrets.token_urlsafe(32)
        self.tool_lock = threading.Lock()
        self.snapshot_lock = threading.RLock()
        self.lifecycle_lock = threading.Lock()
        self.closed = threading.Event()
        self.last_access = time.monotonic()
        self.cached_revision = None
        self.cached_list = None
        self.cached_details = {}
        self.html = ui_path.read_text(encoding="utf-8").replace(
            "<head>", '<head><script src="/browser.js"></script>', 1
        )
        self.script = ui_path.with_name("browser.js").read_text(encoding="utf-8")
        self.script_etag = '"' + hashlib.sha256(self.script.encode("utf-8")).hexdigest() + '"'
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(15)

            def log_message(self, *_args):
                pass  # Never send request URLs or bodies to MCP stdio.

            def reply(self, status, body, content_type="application/json; charset=utf-8", static=False):
                data = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                if status != 304:
                    self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "private, max-age=300" if static else "no-store")
                if static:
                    self.send_header("ETag", owner.script_etag)
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Content-Security-Policy", (
                    "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                    "style-src 'self' 'unsafe-inline'; connect-src 'self'; "
                    "img-src 'self' data:; base-uri 'none'; object-src 'none'; "
                    "frame-ancestors 'none'; form-action 'none'"
                ))
                self.end_headers()
                self.wfile.write(data)

            def valid_host(self):
                return self.headers.get("Host") == owner.origin.removeprefix("http://")

            def do_GET(self):
                if not self.valid_host():
                    return self.reply(403, "{}")
                path = urlsplit(self.path).path
                if path == "/":
                    owner.touch()
                    return self.reply(200, owner.html, "text/html; charset=utf-8")
                if path == "/browser.js":
                    owner.touch()
                    if self.headers.get("If-None-Match") == owner.script_etag:
                        return self.reply(304, "", "application/javascript; charset=utf-8", static=True)
                    return self.reply(200, owner.script, "application/javascript; charset=utf-8", static=True)
                self.reply(404, "{}")

            def do_POST(self):
                token = self.headers.get("X-Config-Token", "")
                if (not self.valid_host() or self.path != "/api/tool"
                        or self.headers.get("Origin") != owner.origin
                        or not token.isascii()
                        or not secrets.compare_digest(token, owner.token)):
                    return self.reply(403, "{}")
                if self.headers.get_content_type() != "application/json":
                    return self.reply(415, "{}")
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 < size <= MAX_REQUEST_BYTES:
                        return self.reply(413, "{}")
                    params = json.loads(self.rfile.read(size))
                except (ValueError, UnicodeDecodeError):
                    return self.reply(400, "{}")
                if (not isinstance(params, dict)
                        or not isinstance(params.get("name"), str)
                        or params["name"] not in CONFIG_TOOLS):
                    return self.reply(403, "{}")
                owner.touch()
                started = time.monotonic()
                try:
                    result = owner.dispatch(params)
                except (ValueError, FileNotFoundError, FileExistsError) as error:
                    result = {"isError": True, "content": [{"type": "text", "text": str(error)}]}
                except Exception:
                    result = {"isError": True, "content": [{
                        "type": "text", "text": "配置操作失败，请检查配置格式及文件访问权限。",
                    }]}
                result.setdefault("_meta", {})["duration_ms"] = round((time.monotonic() - started) * 1000, 2)
                self.reply(200, json.dumps(result, ensure_ascii=False, separators=(",", ":")))

        self.httpd = BoundedHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.origin = f"http://127.0.0.1:{self.httpd.server_port}"
        self.url = f"{self.origin}/#token={self.token}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, name="ssh-config-browser", daemon=True)
        self.thread.start()
        self.reaper = threading.Thread(target=self._expire_idle, name="ssh-config-expiry", daemon=True)
        self.reaper.start()

    @staticmethod
    def _payload(result):
        if result.get("isError"):
            raise ValueError("配置操作失败，请检查配置格式及文件访问权限。")
        if "structuredContent" in result:
            return result["structuredContent"]
        return json.loads(result["content"][0]["text"])

    @staticmethod
    def _result(payload):
        return {"isError": False, "structuredContent": payload}

    def _read_tool(self, name, arguments):
        return self._payload(self.call_tool({"name": name, "arguments": arguments}))

    def snapshot(self, selected=None, require_selected=False):
        # Reads use immutable snapshots instead of the mutation lock.
        with self.snapshot_lock:
            for _attempt in range(3):
                revision = self.config_revision()
                if self.cached_list is None or revision != self.cached_revision:
                    self.cached_list = self._read_tool("ssh_list_servers", {})
                    self.cached_details = {}
                    self.cached_revision = revision
                servers = self.cached_list["servers"]
                valid_names = {item["name"] for item in servers if not item.get("error")}
                name = selected if selected in valid_names else None
                if require_selected and name is None:
                    detail = self._read_tool("ssh_get_server_config", {"server": selected})
                    name = detail["name"]
                    self.cached_details[name] = detail
                if name is None:
                    name = next((item["name"] for item in servers if not item.get("error")), None)
                if name and name not in self.cached_details:
                    self.cached_details[name] = self._read_tool("ssh_get_server_config", {"server": name})
                if revision == self.config_revision():
                    return {
                        "count": len(servers), "servers": servers, "selected": name,
                        "detail": self.cached_details.get(name), "revision": str(revision),
                    }
                self.cached_list = None
            raise ValueError("配置正在更新，请稍后重试。")

    def dispatch(self, params):
        name = params["name"]
        args = params.get("arguments", {})
        if not isinstance(args, dict):
            raise ValueError("配置参数必须是对象。")
        if name == "ssh_ui_bootstrap":
            if set(args) - {"selected"} or (args.get("selected") is not None and not isinstance(args["selected"], str)):
                raise ValueError("无效的服务器选择。")
            return self._result(self.snapshot(args.get("selected")))
        if name == "ssh_list_servers" and not args:
            snapshot = self.snapshot()
            return self._result({key: snapshot[key] for key in ("count", "servers", "revision")})
        if name == "ssh_get_server_config" and set(args) == {"server"} and isinstance(args["server"], str):
            snapshot = self.snapshot(args["server"], require_selected=True)
            return self._result({**snapshot["detail"], "revision": snapshot["revision"]})
        if name in WRITE_TOOLS:
            with self.tool_lock:
                result = self.call_tool(params)
                if result.get("isError"):
                    return result
                snapshot = self.snapshot(None if name == "ssh_ui_delete_server" else args.get("server"))
                return self._result({**self._payload(result), "snapshot": snapshot})
        return self.call_tool(params)

    def touch(self):
        with self.lifecycle_lock:
            self.last_access = time.monotonic()

    @property
    def is_running(self):
        return not self.closed.is_set() and self.thread.is_alive()

    def _expire_idle(self):
        while not self.closed.wait(min(30, self.idle_seconds)):
            with self.lifecycle_lock:
                if time.monotonic() - self.last_access < self.idle_seconds:
                    continue
                self.closed.set()
            self._shutdown()

    def open_browser(self):
        self.touch()
        try:
            result = subprocess.run(
                [sys.executable, "-c", (
                    "import sys, webbrowser; "
                    "sys.exit(0 if webbrowser.open(sys.argv[1], new=2) else 1)"
                ), self.url],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return result.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def wait(self):
        self.thread.join()

    def _shutdown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()

    def close(self):
        with self.lifecycle_lock:
            already_closed = self.closed.is_set()
            self.closed.set()
        if not already_closed:
            self._shutdown()
        elif self.reaper is not threading.current_thread():
            self.reaper.join()
