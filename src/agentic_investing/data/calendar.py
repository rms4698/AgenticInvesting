"""Trading-calendar helpers kept explicit until an exchange calendar is added."""

from datetime import date


# Initial placeholder for the documented scope. This is not a complete holiday
# calendar; production ingestion must use a current authoritative exchange
# calendar before live or time-sensitive research is enabled.
WEEKEND_DAYS = frozenset({5, 6})


def is_weekend(day: date) -> bool:
    """Return whether a date falls on Saturday or Sunday."""

    return day.weekday() in WEEKEND_DAYS
