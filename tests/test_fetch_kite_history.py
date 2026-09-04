import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
for path in (SRC_DIR, SCRIPTS_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from agentic_investing.auth.kite_login import KiteSession
import fetch_kite_history


class SessionFreshnessTests(unittest.TestCase):
    def test_session_is_fresh_before_next_expiry_boundary(self) -> None:
        generated = datetime(2026, 9, 4, 10, 0, tzinfo=timezone.utc)
        session = KiteSession("token", "key", generated)

        self.assertTrue(
            fetch_kite_history._is_session_fresh(
                session, now=generated + timedelta(hours=2)
            )
        )

    def test_session_is_stale_after_expiry_boundary(self) -> None:
        generated = datetime(2026, 9, 4, 10, 0, tzinfo=timezone.utc)
        session = KiteSession("token", "key", generated)
        next_day_after_expiry = datetime(2026, 9, 5, 1, 0, tzinfo=timezone.utc)

        self.assertFalse(fetch_kite_history._is_session_fresh(session, now=next_day_after_expiry))

    def test_resolve_credentials_prefers_environment_variables(self) -> None:
        with mock.patch.dict(
            fetch_kite_history.os.environ,
            {"KITE_API_KEY": "env-key", "KITE_ACCESS_TOKEN": "env-token"},
            clear=True,
        ):
            self.assertEqual(fetch_kite_history._resolve_credentials(), ("env-key", "env-token"))

    def test_resolve_credentials_falls_back_to_fresh_session(self) -> None:
        fresh_session = KiteSession("session-token", "session-key", datetime.now(timezone.utc))
        with mock.patch.dict(fetch_kite_history.os.environ, {}, clear=True), mock.patch.object(
            fetch_kite_history, "load_session", return_value=fresh_session
        ):
            self.assertEqual(
                fetch_kite_history._resolve_credentials(), ("session-key", "session-token")
            )

    def test_resolve_credentials_rejects_stale_session(self) -> None:
        stale_session = KiteSession(
            "session-token", "session-key", datetime.now(timezone.utc) - timedelta(days=2)
        )
        with mock.patch.dict(fetch_kite_history.os.environ, {}, clear=True), mock.patch.object(
            fetch_kite_history, "load_session", return_value=stale_session
        ):
            self.assertIsNone(fetch_kite_history._resolve_credentials())

    def test_resolve_credentials_returns_none_without_any_source(self) -> None:
        with mock.patch.dict(fetch_kite_history.os.environ, {}, clear=True), mock.patch.object(
            fetch_kite_history, "load_session", return_value=None
        ):
            self.assertIsNone(fetch_kite_history._resolve_credentials())


if __name__ == "__main__":
    unittest.main()
