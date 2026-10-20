"""One adapter per market-data source, both returning the same shapes."""

from .base import Provider
from .dnse_provider import DnseProvider
from .yahoo_provider import YahooProvider

__all__ = ["Provider", "DnseProvider", "YahooProvider"]
