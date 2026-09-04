"""Read-only Zerodha Kite Connect historical-data adapter."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Protocol

from ..models import Bar, Timeframe


class KiteHistoricalClient(Protocol):
    """Small subset of the Kite client used by this adapter."""

    def historical_data(
        self,
        instrument_token: int,
        from_date: str,
        to_date: str,
        interval: str,
        continuous: bool = False,
        oi: bool = False,
    ) -> list[dict[str, Any]]: ...


# Kite's historical-data API rejects requests whose date range exceeds a
# per-interval maximum. Values are the officially documented maximum number
# of days per single request.
_MAX_DAYS_PER_REQUEST: dict[Timeframe, int] = {
    "1d": 2000,
    "1h": 400,
    "15m": 200,
    "5m": 100,
    "1m": 60,
}

# Kite labels each candle with the *start* of its interval (e.g. a daily
# candle is dated at that day's market open, not close). The bar's OHLC is
# only fully knowable once the interval actually elapses, so available_at
# must be timestamp + this duration — never equal to timestamp itself, which
# would assert the whole candle (including its close/high/low) was knowable
# at the instant the interval began. This is what data/validation.py's
# LOOKAHEAD_RISK check (available_at < timestamp) is meant to catch, but that
# check cannot detect available_at == timestamp, so getting this duration
# right is essential.
_INTERVAL_DURATION: dict[Timeframe, timedelta] = {
    "1d": timedelta(days=1),
    "1h": timedelta(hours=1),
    "15m": timedelta(minutes=15),
    "5m": timedelta(minutes=5),
    "1m": timedelta(minutes=1),
}


class KiteHistoricalDataProvider:
    """Map Kite historical candles into canonical, UTC-normalized bars.

    This adapter intentionally exposes historical data only. It has no order,
    portfolio, or account methods. Pass an authenticated Kite client created by
    the caller; credentials are never read, stored, or logged by this class.
    Requests spanning more than the interval's maximum range are automatically
    split into sequential chunks and concatenated.
    """

    provider_name = "zerodha-kite-connect"

    def __init__(self, client: KiteHistoricalClient) -> None:
        self._client = client

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
        if instrument_token <= 0:
            raise ValueError("instrument_token must be positive")
        if start > end:
            raise ValueError("start must not be after end")

        interval = {"1d": "day", "1h": "60minute", "15m": "15minute", "5m": "5minute", "1m": "minute"}[timeframe]
        all_candles: list[dict[str, Any]] = []
        for chunk_start, chunk_end in _chunk_date_range(start, end, timeframe):
            candles = self._client.historical_data(
                instrument_token,
                chunk_start.isoformat(),
                chunk_end.isoformat(),
                interval,
                continuous=False,
                oi=False,
            )
            all_candles.extend(candles)

        raw_digest = _digest_candles(all_candles)
        bars = [_to_bar(candle, symbol, exchange, timeframe) for candle in all_candles]
        return bars, raw_digest


def _chunk_date_range(start: date, end: date, timeframe: Timeframe) -> list[tuple[date, date]]:
    """Split a date range into sub-ranges within the interval's max span."""

    max_days = _MAX_DAYS_PER_REQUEST[timeframe]
    chunks: list[tuple[date, date]] = []
    chunk_start = start
    while chunk_start <= end:
        chunk_end = min(chunk_start + timedelta(days=max_days - 1), end)
        chunks.append((chunk_start, chunk_end))
        chunk_start = chunk_end + timedelta(days=1)
    return chunks



def _to_bar(candle: dict[str, Any], symbol: str, exchange: str, timeframe: Timeframe) -> Bar:
    required = ("date", "open", "high", "low", "close", "volume")
    missing = [field for field in required if field not in candle]
    if missing:
        raise ValueError(f"Kite candle is missing fields: {', '.join(missing)}")
    timestamp = _to_utc(candle["date"])
    return Bar(
        instrument=symbol,
        exchange=exchange.upper(),
        timeframe=timeframe,
        timestamp=timestamp,
        available_at=timestamp + _INTERVAL_DURATION[timeframe],
        open=Decimal(str(candle["open"])),
        high=Decimal(str(candle["high"])),
        low=Decimal(str(candle["low"])),
        close=Decimal(str(candle["close"])),
        volume=int(candle["volume"]),
    )


def _to_utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError(f"unsupported Kite candle timestamp: {value!r}")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _digest_candles(candles: list[dict[str, Any]]) -> str:
    import hashlib
    import json

    payload = json.dumps(candles, default=str, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
