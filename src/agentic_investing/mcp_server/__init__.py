"""MCP tool server exposing risk-gated read+propose tools to any MCP client."""

from .server import main, server

__all__ = ["main", "server"]
