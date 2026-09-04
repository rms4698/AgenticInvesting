"""Yahoo Finance daily/intraday research-data adapter.

Yahoo Finance is useful for exploratory research, but it is not the project's
production or broker-authoritative source. Verify data licensing and quality
before using it beyond personal research.
"""

from datetime import date, datetime, timezone
import base64
import hashlib
import json
import os
import ssl
import tempfile
from decimal import Decimal
from typing import Any, Callable, Protocol

from ..models import Bar, Timeframe


class YahooDownloader(Protocol):
    """Subset of yfinance.download used by the provider."""

    def __call__(self, *args: Any, **kwargs: Any) -> Any: ...


class YahooSession(Protocol):
    """Protocol for the curl_cffi session accepted by yfinance."""


class YahooFinanceDataProvider:
    """Fetch Yahoo Finance candles and normalize them into canonical bars."""

    provider_name = "yahoo-finance"

    def __init__(
        self,
        downloader: YahooDownloader | None = None,
        session: YahooSession | None = None,
    ) -> None:
        self._downloader = downloader or self._default_downloader()
        self._session = session or _default_session()

    @staticmethod
    def _default_downloader() -> Callable[..., Any]:
        try:
            import yfinance as yf
        except ImportError as error:
            raise RuntimeError("Install yfinance to use YahooFinanceDataProvider") from error
        return yf.download

    def historical_bars(
        self,
        *,
        instrument_token: int = 0,
        symbol: str,
        exchange: str,
        timeframe: Timeframe,
        start: date,
        end: date,
    ) -> tuple[list[Bar], str]:
        del instrument_token
        if not symbol.strip():
            raise ValueError("symbol must not be empty")
        if start > end:
            raise ValueError("start must not be after end")
        if timeframe not in {"1d", "1h", "15m", "5m", "1m"}:
            raise ValueError(f"unsupported timeframe: {timeframe}")

        interval = {"1d": "1d", "1h": "60m", "15m": "15m", "5m": "5m", "1m": "1m"}[timeframe]
        # Yahoo's end date is exclusive; request one day beyond the user's end.
        end_exclusive = end.toordinal() + 1
        end_date = date.fromordinal(end_exclusive)
        frame = self._downloader(
            symbol,
            start=start.isoformat(),
            end=end_date.isoformat(),
            interval=interval,
            auto_adjust=False,
            actions=False,
            progress=False,
            threads=False,
            session=self._session,
        )
        records = _records_from_frame(frame)
        raw_digest = _digest_records(records)
        bars = [_to_bar(record, symbol=symbol, exchange=exchange, timeframe=timeframe) for record in records]
        return bars, raw_digest


def _records_from_frame(frame: Any) -> list[dict[str, Any]]:
    """Convert a yfinance DataFrame into records without requiring pandas here."""

    if frame is None or getattr(frame, "empty", False):
        return []
    columns = getattr(frame, "columns", ())
    if hasattr(columns, "get_level_values") and getattr(columns, "nlevels", 1) > 1:
        frame = frame.copy()
        frame.columns = [column[0] if isinstance(column, tuple) else column for column in frame.columns]
    required = {"Open", "High", "Low", "Close", "Volume"}
    if not required.issubset(set(frame.columns)):
        raise ValueError("Yahoo response is missing OHLCV columns")

    records: list[dict[str, Any]] = []
    for index, row in frame.iterrows():
        timestamp = index.to_pydatetime() if hasattr(index, "to_pydatetime") else index
        if not isinstance(timestamp, datetime):
            raise ValueError(f"unsupported Yahoo timestamp: {timestamp!r}")
        records.append(
            {
                "date": timestamp,
                "open": row["Open"],
                "high": row["High"],
                "low": row["Low"],
                "close": row["Close"],
                "volume": row["Volume"],
            }
        )
    return records


def _to_bar(record: dict[str, Any], *, symbol: str, exchange: str, timeframe: Timeframe) -> Bar:
    timestamp = record["date"]
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    timestamp = timestamp.astimezone(timezone.utc)
    return Bar(
        instrument=symbol,
        exchange=exchange.upper(),
        timeframe=timeframe,
        timestamp=timestamp,
        available_at=timestamp,
        open=Decimal(str(record["open"])),
        high=Decimal(str(record["high"])),
        low=Decimal(str(record["low"])),
        close=Decimal(str(record["close"])),
        volume=int(record["volume"]),
    )


def _digest_records(records: list[dict[str, Any]]) -> str:
    payload = json.dumps(records, default=str, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _default_session() -> YahooSession:
    """Create a verified curl session using certifi and Windows trusted roots.

    Some Windows environments inspect HTTPS with a locally trusted root (for
    example, Netskope). curl_cffi uses OpenSSL and does not automatically use
    that Windows store, so we build a temporary public-CA bundle. Verification
    and hostname checking remain enabled; no certificate bypass is used.
    """

    try:
        import certifi
        from curl_cffi import requests
    except ImportError as error:
        raise RuntimeError("Install yfinance to use YahooFinanceDataProvider") from error

    bundle = _windows_ca_bundle(certifi.where())
    return requests.Session(impersonate="chrome", verify=str(bundle))


def _windows_ca_bundle(certifi_path: str) -> str:
    """Return a cached bundle containing certifi and Windows ROOT certificates."""

    if os.name != "nt" or not hasattr(ssl, "enum_certificates"):
        return certifi_path

    bundle_path = os.path.join(tempfile.gettempdir(), "agentic-investing-yahoo-ca.pem")
    if os.path.exists(bundle_path):
        return bundle_path

    pem_parts = [open(certifi_path, "rb").read()]
    for der, encoding, _trust in ssl.enum_certificates("ROOT"):
        if encoding != "x509_asn1":
            continue
        pem_parts.extend(
            (
                b"\n-----BEGIN CERTIFICATE-----\n",
                base64.b64encode(der),
                b"\n-----END CERTIFICATE-----\n",
            )
        )
    with open(bundle_path, "wb") as handle:
        handle.write(b"".join(pem_parts))
    return bundle_path
