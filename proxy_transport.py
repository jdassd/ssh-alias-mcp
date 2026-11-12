"""HTTP CONNECT and legacy SOCKS5 transports shared by SSH operations."""
import base64
import json
import math
import os
from pathlib import Path
import re
import socket
import time
from urllib.parse import urlsplit


def default_http_proxy():
    stable_endpoint = ("127.0.0.2", 18080) if os.name == "nt" else ("127.0.0.1", 18081)
    host, port = stable_endpoint
    endpoint = os.environ.get("VPN_HTTP_PROXY_URL", "").strip()
    if endpoint:
        parsed = urlsplit(endpoint)
        if (parsed.scheme != "http" or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.path not in ("", "/")
                or parsed.query or parsed.fragment):
            raise ValueError("VPN_HTTP_PROXY_URL must be http://host:port without credentials")
        host, port = parsed.hostname, parsed.port if parsed.port is not None else 80
        # Validate before recognizing a historical installer-generated default.
        validate_proxy({"host": host, "port": port})
        legacy_endpoints = {("127.0.0.1", 18080)}
        legacy_url = os.environ.get("VPN_HTTP_PROXY_LEGACY_URL", "").strip()
        if legacy_url:
            legacy = urlsplit(legacy_url)
            if (legacy.scheme != "http" or not legacy.hostname or legacy.username is not None
                    or legacy.password is not None or legacy.path not in ("", "/")
                    or legacy.query or legacy.fragment):
                raise ValueError("VPN_HTTP_PROXY_LEGACY_URL must be http://host:port without credentials")
            legacy_endpoint = (legacy.hostname, legacy.port if legacy.port is not None else 80)
            validate_proxy({"host": legacy_endpoint[0], "port": legacy_endpoint[1]})
            legacy_endpoints.add(legacy_endpoint)
        if ((host, port) in legacy_endpoints
                and os.environ.get("VPN_HTTP_PROXY_KEEP_LEGACY_ENDPOINT", "").strip() != "1"):
            host, port = stable_endpoint
    result = {
        "enabled": True,
        "type": "http",
        "host": host,
        "port": port,
        "auth_file": os.environ.get("VPN_HTTP_PROXY_AUTH_FILE", "").strip()
        or str(Path.home() / ".config" / "openvpn-http-proxy" / "proxy-auth.json"),
    }
    validate_proxy(result)
    return result


def resolve_proxy(server_proxy, global_config):
    """Missing values inherit; explicit disabled values always select direct TCP."""
    configured = server_proxy
    if configured is None or configured == {}:
        configured = global_config.get("proxy")
    if configured is False:
        return None
    if configured is None or configured == {}:
        return default_http_proxy()
    if not isinstance(configured, dict):
        raise ValueError("proxy must be an object, false, or null")
    if configured.get("enabled") is False:
        return None
    # Previously stored endpoint-only proxy objects represented SOCKS5.
    protocol = str(configured.get("type") or (
        "socks5" if "host" in configured or "port" in configured else "http"
    )).lower()
    if protocol not in ("http", "socks5"):
        raise ValueError("proxy.type must be http or socks5")
    base = default_http_proxy() if protocol == "http" else {
        "enabled": True, "type": "socks5", "host": "127.0.0.1", "port": 1080,
    }
    base.update(configured)
    base["type"] = protocol
    validate_proxy(base)
    return base


def validate_proxy(proxy):
    if proxy is None or proxy is False:
        return
    if not isinstance(proxy, dict):
        raise ValueError("server.proxy must be an object, false, or null")
    if "enabled" in proxy and not isinstance(proxy["enabled"], bool):
        raise ValueError("proxy.enabled must be true or false")
    if proxy.get("enabled") is False:
        return
    if "type" in proxy and proxy["type"] not in ("http", "socks5"):
        raise ValueError("proxy.type must be http or socks5")
    if "host" in proxy and (not isinstance(proxy["host"], str) or not proxy["host"].strip()
                            or any(c.isspace() or ord(c) < 32 for c in proxy["host"])
                            or any(c in proxy["host"] for c in "/@\\")):
        raise ValueError("proxy.host must be a nonempty host name or IP")
    if "port" in proxy:
        try:
            valid_port = not isinstance(proxy["port"], bool) and 1 <= int(proxy["port"]) <= 65535
        except (TypeError, ValueError):
            valid_port = False
        if not valid_port:
            raise ValueError("proxy.port must be between 1 and 65535")
    for key in ("username", "password", "auth_file"):
        if key in proxy and proxy[key] is not None and not isinstance(proxy[key], str):
            raise ValueError(f"proxy.{key} must be a string or null")
    if "timeout" in proxy:
        try:
            valid_timeout = math.isfinite(float(proxy["timeout"])) and float(proxy["timeout"]) > 0
        except (TypeError, ValueError):
            valid_timeout = False
        if not valid_timeout:
            raise ValueError("proxy.timeout must be positive")


def proxy_credentials(proxy):
    username, password = proxy.get("username"), proxy.get("password")
    if username is not None or password is not None:
        if not isinstance(username, str) or not isinstance(password, str):
            raise ValueError("Proxy username and password must be configured together")
    else:
        auth_file = proxy.get("auth_file")
        if not auth_file:
            return None, None
        path = Path(os.path.expandvars(os.path.expanduser(auth_file)))
        try:
            auth = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            raise ValueError("Cannot read proxy auth_file; configure VPN_HTTP_PROXY_AUTH_FILE or proxy.auth_file") from None
        if not isinstance(auth, dict):
            raise ValueError("Proxy auth_file must contain username and password")
        username, password = auth.get("username"), auth.get("password")
        if not isinstance(username, str) or not isinstance(password, str) or not username or not password:
            raise ValueError("Proxy auth_file must contain nonempty username and password")
    if ":" in username or any(c in username + password for c in "\r\n"):
        raise ValueError("Invalid proxy credentials")
    return username, password


def open_proxy_socket(proxy, target_host, target_port, timeout):
    """Return a connected tunnel, never falling back to a direct target socket."""
    validate_proxy(proxy)
    timeout = float(proxy.get("timeout", timeout))
    username, password = proxy_credentials(proxy)
    if proxy["type"] == "socks5":
        import socks
        stream = socks.socksocket()
        try:
            stream.set_proxy(socks.SOCKS5, proxy["host"], int(proxy["port"]),
                             rdns=True, username=username, password=password)
            stream.settimeout(timeout)
            stream.connect((target_host, int(target_port)))
            return stream
        except Exception:
            stream.close()
            raise

    host = str(target_host).strip("[]")
    if not host or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in host):
        raise ValueError("Invalid HTTP CONNECT target")
    if not 1 <= int(target_port) <= 65535:
        raise ValueError("Invalid HTTP CONNECT target port")
    host = host.encode("idna").decode("ascii")
    authority = f"[{host}]:{int(target_port)}" if ":" in host else f"{host}:{int(target_port)}"
    lines = [f"CONNECT {authority} HTTP/1.1", f"Host: {authority}"]
    if username is not None:
        token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        lines.append("Proxy-Authorization: Basic " + token)
    deadline = time.monotonic() + timeout
    stream = socket.create_connection((proxy["host"], int(proxy["port"])), timeout)
    try:
        stream.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("ascii"))
        header = bytearray()
        while not header.endswith(b"\r\n\r\n"):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("HTTP CONNECT handshake timed out")
            if len(header) >= 65536:
                raise ConnectionError("HTTP CONNECT response headers are too large")
            stream.settimeout(remaining)
            byte = stream.recv(1)
            if not byte:
                raise ConnectionError("HTTP proxy closed the CONNECT handshake")
            header.extend(byte)
        # Read only headers; never consume the SSH banner that can follow them.
        match = re.fullmatch(rb"HTTP/1\.[01] ([0-9]{3})(?: [^\r\n]*)?", bytes(header).split(b"\r\n", 1)[0])
        if not match:
            raise ConnectionError("Invalid HTTP CONNECT proxy response")
        status = int(match[1])
        if not 200 <= status < 300:
            raise ConnectionError(f"HTTP CONNECT proxy rejected the tunnel (status {status})")
        stream.settimeout(timeout)
        return stream
    except Exception:
        stream.close()
        raise
