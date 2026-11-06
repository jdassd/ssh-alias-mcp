"""Loopback-only browser host for the SSH server configuration page."""
import json
from pathlib import Path
import secrets
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


CONFIG_TOOLS = frozenset({
    "ssh_list_servers",
    "ssh_get_server_config",
    "ssh_ui_create_server",
    "ssh_ui_update_server",
    "ssh_ui_delete_server",
})
MAX_REQUEST_BYTES = 128 * 1024


class ConfigWebServer:
    """Share the MCP configuration handlers with a local browser session."""

    def __init__(self, ui_path: Path, call_tool):
        self.ui_path = ui_path
        self.call_tool = call_tool
        self.token = secrets.token_urlsafe(32)
        self.tool_lock = threading.Lock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(15)

            def log_message(self, *_args):
                # URLs and request bodies must never enter the MCP stdio stream.
                pass

            def reply(self, status, body, content_type="application/json; charset=utf-8"):
                data = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
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
                    html = owner.ui_path.read_text(encoding="utf-8")
                    html = html.replace(
                        "<head>", '<head><script src="/browser.js"></script>', 1
                    )
                    return self.reply(200, html, "text/html; charset=utf-8")
                if path == "/browser.js":
                    script = owner.ui_path.with_name("browser.js").read_text(encoding="utf-8")
                    return self.reply(200, script, "application/javascript; charset=utf-8")
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
                try:
                    with owner.tool_lock:
                        result = owner.call_tool(params)
                except (ValueError, FileNotFoundError, FileExistsError) as error:
                    result = {"isError": True, "content": [{
                        "type": "text", "text": str(error),
                    }]}
                except Exception:
                    result = {"isError": True, "content": [{
                        "type": "text", "text": "配置操作失败，请检查配置格式及文件访问权限。",
                    }]}
                self.reply(200, json.dumps(result, ensure_ascii=False))

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.origin = f"http://127.0.0.1:{self.httpd.server_port}"
        self.url = f"{self.origin}/#token={self.token}"
        self.thread = threading.Thread(
            target=self.httpd.serve_forever, name="ssh-config-browser", daemon=True
        )
        self.thread.start()

    def open_browser(self):
        try:
            # A browser launcher may print to stdout; keep MCP stdio JSON-only.
            result = subprocess.run(
                [sys.executable, "-c", (
                    "import sys, webbrowser; "
                    "sys.exit(0 if webbrowser.open(sys.argv[1], new=2) else 1)"
                ), self.url],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return result.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def wait(self):
        self.thread.join()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()
