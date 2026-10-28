"""Neshan (مسیریاب نشان) helpers.

The venue address shown across the site links to the Neshan web router so
Iranian visitors get a Persian, Iran-accurate map instead of a foreign
provider. The admin can pin an exact location by filling ``site.map_url``;
otherwise the link is derived from the address text.
"""
from __future__ import annotations

from urllib.parse import quote_plus

NESHAN_SEARCH = "https://neshan.org/maps/search/{query}"


def neshan_search_url(address: str) -> str:
    """Return a Neshan web-search URL for an address (empty input -> empty URL)."""
    address = (address or "").strip()
    if not address:
        return ""
    return NESHAN_SEARCH.format(query=quote_plus(address))
