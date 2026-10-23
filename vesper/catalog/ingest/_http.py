"""One HTTP helper for every adapter, so failures always name the URL."""
from __future__ import annotations

import httpx

from vesper import __version__
from vesper.catalog.errors import IngestError


def default_client() -> httpx.Client:
    return httpx.Client(timeout=60.0, follow_redirects=True, headers={"User-Agent": f"vesper-catalog/{__version__}"})


def fetch(client: httpx.Client, url: str, params: dict | None = None) -> httpx.Response:
    try:
        response = client.get(url, params=params)
        response.raise_for_status()
    except httpx.HTTPError as e:
        raise IngestError(f"download failed: {url}: {e}") from e
    return response
