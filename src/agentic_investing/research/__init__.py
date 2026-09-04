"""Read-only external research tools (news, fundamentals, technical indicators).

Everything here is a plain data client, never an agent — see
``alpha_vantage.py`` for the risk-boundary rationale.
"""

from .alpha_vantage import AlphaVantageClient, AlphaVantageError, CompanyOverview, NewsArticle, to_alpha_vantage_symbol

__all__ = [
    "AlphaVantageClient",
    "AlphaVantageError",
    "CompanyOverview",
    "NewsArticle",
    "to_alpha_vantage_symbol",
]
