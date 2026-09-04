import json
import sys
import tempfile
import threading
import unittest
from unittest import mock
from pathlib import Path
from urllib.request import urlopen

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agentic_investing.auth.kite_login import (
    KiteLoginConfig,
    KiteSession,
    authenticate_kite,
    load_session,
    save_session,
)
import agentic_investing.auth.kite_login as kite_login


class FakeKite:
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self.generated = None

    def login_url(self) -> str:
        return "http://127.0.0.1:8765/kite-redirect-login"

    def generate_session(self, request_token: str, api_secret: str):
        self.generated = (request_token, api_secret)
        return {"access_token": "daily-token"}


class KiteAuthTests(unittest.TestCase):
    def test_user_environment_fallback_is_used_when_process_environment_is_empty(self) -> None:
        with mock.patch.dict(kite_login.os.environ, {}, clear=True), mock.patch(
            "agentic_investing.auth.kite_login._read_windows_user_environment",
            side_effect=lambda name: {"KITE_API_KEY": "user-key", "KITE_API_SECRET": "user-secret"}.get(name),
        ):
            self.assertEqual(kite_login._local_setting("KITE_API_KEY"), "user-key")
            self.assertEqual(kite_login._local_setting("KITE_API_SECRET"), "user-secret")

    def test_session_round_trip(self) -> None:
        session = KiteSession("token", "key", __import__("datetime").datetime.now(__import__("datetime").timezone.utc))
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "session.json"
            save_session(session, path)
            loaded = load_session(path)

            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.access_token, "token")
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotIn("api_secret", payload)

    def test_authentication_exchanges_callback_token(self) -> None:
        config = KiteLoginConfig(port=18765)
        fake_kite = None

        def factory(api_key: str) -> FakeKite:
            nonlocal fake_kite
            fake_kite = FakeKite(api_key)
            return fake_kite

        def browser(_url: str) -> bool:
            def callback() -> None:
                urlopen(
                    "http://127.0.0.1:18765/kite-redirect?request_token=request-123&status=success",
                    timeout=5,
                ).read()

            threading.Thread(target=callback, daemon=True).start()
            return True

        with tempfile.TemporaryDirectory() as temp_dir:
            session = authenticate_kite(
                config=config,
                api_key="api-key",
                api_secret="secret",
                kite_factory=factory,
                open_browser=browser,
                session_file=Path(temp_dir) / "session.json",
            )

        self.assertEqual(session.access_token, "daily-token")
        assert fake_kite is not None
        self.assertEqual(fake_kite.generated, ("request-123", "secret"))


if __name__ == "__main__":
    unittest.main()
