"""Local Zerodha Kite Connect login and daily-session storage.

The API key and secret are read only from environment variables. The browser
handles Zerodha credentials and TOTP; this module never prompts for or stores
them. The access token is written outside the repository under LOCALAPPDATA.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Event, Thread
from typing import Any, Callable, cast
from urllib.parse import parse_qs, urlparse
import webbrowser

from kiteconnect import KiteConnect

if sys.platform == "win32":
    import winreg


@dataclass(frozen=True, slots=True)
class KiteLoginConfig:
    """Local callback settings matching the registered Zerodha redirect URL."""

    host: str = "127.0.0.1"
    port: int = 8765
    path: str = "/kite-redirect"
    timeout_seconds: int = 300

    @property
    def redirect_url(self) -> str:
        return f"http://{self.host}:{self.port}{self.path}"


@dataclass(frozen=True, slots=True)
class KiteSession:
    """Non-secret session metadata plus the daily access token."""

    access_token: str
    api_key: str
    generated_at: datetime


class _CallbackHandler(BaseHTTPRequestHandler):
    server_version = "AgenticInvestingKiteCallback/1.0"

    def do_GET(self) -> None:  # noqa: N802
        expected_path = self.server.callback_path  # type: ignore[attr-defined]
        parsed = urlparse(self.path)
        if parsed.path != expected_path:
            self.send_error(404)
            return

        params = parse_qs(parsed.query)
        request_token = params.get("request_token", [""])[0]
        status = params.get("status", [""])[0]
        self.server.callback_result = (request_token, status)  # type: ignore[attr-defined]
        self.server.callback_event.set()  # type: ignore[attr-defined]
        body = b"Kite login received. You may close this browser tab."
        self.send_response(200 if request_token else 400)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        del format, args


def session_path() -> Path:
    """Return the OS-local session path outside the repository."""

    root = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local"))
    return root / "AgenticInvesting" / "kite-session.json"


def save_session(session: KiteSession, path: str | Path | None = None) -> Path:
    """Atomically save the daily access token with restrictive permissions."""

    destination = Path(path) if path else session_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "access_token": session.access_token,
        "api_key": session.api_key,
        "generated_at": session.generated_at.isoformat(),
    }
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    if os.name == "nt":
        _restrict_windows_acl(temporary)
    else:
        temporary.chmod(0o600)
    temporary.replace(destination)
    if os.name == "nt":
        _restrict_windows_acl(destination)
    else:
        destination.chmod(0o600)
    return destination


def load_session(path: str | Path | None = None) -> KiteSession | None:
    """Load a locally stored session, returning None for missing/invalid data.

    A genuine I/O failure (permission denied, disk error, locked file) is
    deliberately NOT treated the same as "no session file exists" — it is
    allowed to propagate, since silently returning None for both would look
    identical to a caller (triggering an unnecessary and repeated re-login)
    while hiding a real, actionable root cause (e.g. a broken ACL from
    ``_restrict_windows_acl``). Only errors indicating the *content* is
    missing or malformed return None.
    """

    source = Path(path) if path else session_path()
    if not source.exists():
        return None
    text = source.read_text(encoding="utf-8")  # OSError here propagates.
    try:
        payload = json.loads(text)
        access_token = str(payload["access_token"])
        api_key = str(payload["api_key"])
        generated_at = datetime.fromisoformat(payload["generated_at"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if generated_at.tzinfo is None:
        return None
    if not access_token or not api_key:
        return None
    return KiteSession(access_token, api_key, generated_at.astimezone(timezone.utc))


def _read_windows_user_environment(name: str) -> str | None:
    """Read a user environment variable for terminals opened before a change."""

    if os.name != "nt":
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:  # type: ignore[name-defined]
            value, _value_type = winreg.QueryValueEx(key, name)
    except (FileNotFoundError, OSError, ImportError):
        return None
    return str(value) if value else None


def _local_setting(name: str, explicit_value: str | None = None) -> str | None:
    """Resolve explicit, process, then Windows-user environment settings."""

    return explicit_value or os.environ.get(name) or _read_windows_user_environment(name)


def authenticate_kite(
    *,
    config: KiteLoginConfig | None = None,
    api_key: str | None = None,
    api_secret: str | None = None,
    kite_factory: Callable[[str], Any] | None = None,
    open_browser: Callable[[str], bool] = webbrowser.open,
    session_file: str | Path | None = None,
) -> KiteSession:
    """Run browser login, exchange request token, and save the daily session."""

    actual_config = config or KiteLoginConfig()
    actual_api_key = _local_setting("KITE_API_KEY", api_key)
    actual_api_secret = _local_setting("KITE_API_SECRET", api_secret)
    if not actual_api_key or not actual_api_secret:
        raise ValueError("KITE_API_KEY and KITE_API_SECRET must be set locally")
    if kite_factory is None:
        kite_factory = KiteConnect

    kite = kite_factory(actual_api_key)
    server = HTTPServer((actual_config.host, actual_config.port), _CallbackHandler)
    server.callback_path = actual_config.path  # type: ignore[attr-defined]
    server.callback_event = Event()  # type: ignore[attr-defined]
    server.callback_result = ("", "")  # type: ignore[attr-defined]
    callback_server = cast(Any, server)
    server_thread = Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        login_url = kite.login_url()
        print(f"Complete Zerodha login in the browser: {login_url}")
        if not open_browser(login_url):
            raise RuntimeError("could not open browser; open the printed login URL manually")
        if not callback_server.callback_event.wait(actual_config.timeout_seconds):
            raise TimeoutError("timed out waiting for Zerodha callback")
        request_token, status = callback_server.callback_result
        if status != "success" or not request_token:
            raise RuntimeError("Zerodha login did not return a request token")
        data = kite.generate_session(request_token, api_secret=actual_api_secret)
        access_token = str(cast(dict[str, Any], data)["access_token"])
    finally:
        server.shutdown()
        server_thread.join(timeout=2)
        server.server_close()

    session = KiteSession(access_token, actual_api_key, datetime.now(timezone.utc))
    save_session(session, session_file)
    return session


def _restrict_windows_acl(path: Path) -> None:
    """Restrict a file to the current Windows identity; fail closed."""

    identity = subprocess.check_output(["whoami"], text=True).strip()
    result = subprocess.run(
        ["icacls", str(path), "/inheritance:r", "/grant:r", f"{identity}:(F)"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        path.unlink(missing_ok=True)
        raise OSError(f"could not restrict session-file permissions: {result.stderr.strip()}")
