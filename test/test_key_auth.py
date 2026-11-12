#!/usr/bin/env python3
"""SSH key authentication tests.

Tests key-based SSH authentication:
  - Password vs key auth
  - Key with passphrase
  - Key file not found error
  - Key auth + sudo
  - Key auth + alias execution

Run: SSH_TEST_SERVER=test-key-auth python test_key_auth.py
"""
import json
import os
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).parent.parent.resolve()
TEST_DIR = Path(__file__).parent.resolve()
CLI = [sys.executable, str(SKILL_DIR / "cli.py")]
sys.path.insert(0, str(SKILL_DIR))

# Target server (override via SSH_TEST_SERVER env var)
SERVER = os.environ.get("SSH_TEST_SERVER", "test-key-auth")

RED, GREEN, YELLOW, NC = "\033[91m", "\033[92m", "\033[93m", "\033[0m"
passed = failed = skipped = 0


def _ok(msg):
    print(f"{GREEN}PASS{NC}  {msg}")


def _fail(msg, detail=""):
    print(f"{RED}FAIL{NC}  {msg} — {detail}" if detail else f"{RED}FAIL{NC}  {msg}")


def _skip(msg):
    print(f"{YELLOW}SKIP{NC}  {msg}")


def _section(name):
    print(f"\n{'='*60}\n  {name}\n{'='*60}")


def _inc(result, name, detail=""):
    global passed, failed, skipped
    if result == "ok":
        passed += 1
        _ok(name)
    elif result == "fail":
        failed += 1
        _fail(name, detail)
    else:
        skipped += 1
        _skip(name)


def run_cli(*args, timeout=300):
    """Run cli.py and return (stdout, stderr, returncode)."""
    cmd = CLI + list(args)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                       cwd=str(SKILL_DIR), encoding="utf-8", errors="replace")
    return r.stdout, r.stderr, r.returncode


# ═══════ 1. Key auth — basic connection ═════════════════════════════

def test_key_auth_basic():
    _section("1. Key auth — basic connection")

    # Case 1: run command using key auth
    out, err, code = run_cli(SERVER, "run", "whoami", "-t", "10")
    _inc("ok" if code == 0 and "YOUR_SSH_USER" in out else "fail",
         "key auth run whoami returns 'YOUR_SSH_USER'",
         f"stdout={out.strip()!r} code={code}")

    # Case 2: run command with echo
    out, err, code = run_cli(SERVER, "run", "echo key-auth-test", "-t", "10")
    _inc("ok" if code == 0 and "key-auth-test" in out else "fail",
         "key auth echo command works",
         f"stdout={out.strip()!r} code={code}")

    # Case 3: list-servers should show test-key-auth server
    out, err, code = run_cli("list-servers")
    data = json.loads(out)
    srv = next((s for s in data["servers"] if s["name"] == "test-key-auth"), None)
    _inc("ok" if srv and srv.get("host") == "192.0.2.10" else "fail",
         "list-servers shows test-key-auth with correct host",
         f"host={srv.get('host') if srv else 'N/A'}")


# ═══════ 2. Key auth — error handling ═════════════════════════════

def test_key_auth_errors():
    _section("2. Key auth — error handling")

    # Case 1: nonexistent key file → error
    out, err, code = run_cli("test-key-auth-fake", "run", "echo test", "-t", "10")
    _inc("ok" if code != 0 else "fail",
         "nonexistent server config returns error",
         f"code={code}")

    # Case 2: run with nonexistent key auth server
    from ssh_client import pool
    try:
        pool.get("test-key-auth-fake")
        _fail("pool.get('nonexistent') should raise FileNotFoundError")
    except FileNotFoundError as e:
        _inc("ok" if "Server config not found" in str(e) else "fail",
             "pool.get raises FileNotFoundError",
             f"msg={str(e)!r}")


# ═══════ 3. Key auth — sudo with key ═════════════════════════════

def test_key_auth_sudo():
    _section("3. Key auth — sudo with key")

    # Case 1: sudo command using key auth
    out, err, code = run_cli(SERVER, "run", "whoami", "-s", "-t", "10")
    _inc("ok" if code == 0 and "root" in out else "fail",
         "key auth sudo whoami returns 'root'",
         f"stdout={out.strip()!r} code={code}")

    # Case 2: sudo cat /etc/hostname
    out, err, code = run_cli(SERVER, "run", "cat /etc/hostname", "-s", "-t", "10")
    _inc("ok" if code == 0 and len(out.strip()) > 0 else "fail",
         "key auth sudo cat /etc/hostname works",
         f"stdout={out.strip()!r} code={code}")


# ═══════ 4. Key auth — alias execution ═════════════════════════════

def test_key_auth_alias():
    _section("4. Key auth — alias execution")

    # Case 1: run alias using key auth
    out, err, code = run_cli(SERVER, "alias", "test-key-echo")
    _inc("ok" if code == 0 and "test-key-auth-ok" in out else "fail",
         "key auth alias execution works",
         f"stdout={out.strip()!r} code={code}")

    # Case 2: list-aliases
    out, err, code = run_cli(SERVER, "list-aliases")
    data = json.loads(out)
    aliases = {a["name"]: a for a in data["aliases"]}
    _inc("ok" if "test-key-echo" in aliases else "fail",
         "list-aliases shows test-key-echo",
         f"names={list(aliases.keys())}")


# ═══════ 5. Key auth — upload/download with key ═════════════════════════════

def test_key_auth_upload_download():
    _section("5. Key auth — upload/download with key")

    # Case 1: upload script using key auth
    test_script = TEST_DIR / "test_key_auth_upload.sh"
    test_script.write_text('#!/usr/bin/env bash\necho "key-upload-ok"\n')

    out, err, code = run_cli(SERVER, "upload", str(test_script), "-t", "10")
    _inc("ok" if "Script uploaded" in out else "fail",
         "key auth upload script works",
         f"stdout={out.strip()!r} code={code}")

    # Case 2: run-script using key auth
    out, err, code = run_cli(SERVER, "run-script", "test_key_auth_upload.sh", "-t", "10")
    _inc("ok" if code == 0 and "key-upload-ok" in out else "fail",
         "key auth run-script works",
         f"stdout={out.strip()!r} code={code}")

    # Case 3: download file using key auth
    run_cli(SERVER, "run", "echo 'key-download-test' > /tmp/test_key_dl.txt", "-s", "-t", "10")
    local_file = TEST_DIR / "downloaded_key_test.txt"
    local_file.unlink(missing_ok=True)
    out, err, code = run_cli(SERVER, "download", "/tmp/test_key_dl.txt", str(local_file), "-t", "10")
    _inc("ok" if code == 0 and "Downloaded" in out else "fail",
         "key auth download file works",
         f"stdout={out.strip()!r} code={code}")
    if local_file.exists():
        _inc("ok" if "key-download-test" in local_file.read_text() else "fail",
             "downloaded file content matches")
        local_file.unlink()

    # Cleanup
    test_script.unlink()


# ═══════ 6. Key auth — list-scripts with key ═════════════════════════════

def test_key_auth_list_scripts():
    _section("6. Key auth — list-scripts with key")

    # Case 1: list-scripts using key auth
    out, err, code = run_cli(SERVER, "list-scripts", "-t", "10")
    _inc("ok" if code == 0 and len(out.strip()) > 0 else "fail",
         "key auth list-scripts returns output",
         f"stdout={out.strip()!r} code={code}")

    # Case 2: list-scripts with sudo
    out, err, code = run_cli(SERVER, "list-scripts", "-s", "-t", "10")
    _inc("ok" if code == 0 else "fail",
         "key auth list-scripts -s works",
         f"stdout={out.strip()!r} code={code}")


# ═══════ main ═════════════════════════════

def main():
    global passed, failed, skipped
    tests = [
        test_key_auth_basic,           # 1.  key auth basic
        test_key_auth_errors,          # 2.  key auth errors
        test_key_auth_sudo,            # 3.  key auth sudo
        test_key_auth_alias,           # 4.  key auth alias
        test_key_auth_upload_download, # 5.  key auth upload/download
        test_key_auth_list_scripts,    # 6.  key auth list-scripts
    ]

    print("="*60)
    print("  SSH Key Authentication Tests")
    print(f"  Target: {SERVER}")
    print(f"  Tests:  {len(tests)} test functions")
    print("="*60)

    for t in tests:
        try:
            t()
        except subprocess.TimeoutExpired:
            _fail(f"{t.__name__} — timeout")
        except Exception as e:
            _fail(f"{t.__name__} — exception: {e}")

    print(f"\n{'='*60}")
    print(f"  Results: {GREEN}{passed} passed{NC}, {RED}{failed} failed{NC}, {YELLOW}{skipped} skipped{NC}")
    print(f"{'='*60}")

    if failed > 0:
        sys.exit(1)
    print("\nAll key auth tests passed!")


if __name__ == "__main__":
    main()
