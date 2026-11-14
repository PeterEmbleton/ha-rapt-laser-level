"""Look up a drink's picture (and name) from a product page URL."""

from __future__ import annotations

import html
import logging
import re
from urllib.parse import urljoin

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)

_META_RE = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
_ATTR_RE = re.compile(r'([a-zA-Z:_-]+)\s*=\s*("([^"]*)"|\'([^\']*)\')')


def parse_meta(page: str, base_url: str) -> tuple[str | None, str | None]:
    """Return (image_url, title) from og:/twitter: meta tags."""
    found: dict[str, str] = {}
    for tag in _META_RE.findall(page):
        attrs = {
            m.group(1).lower(): html.unescape(m.group(3) if m.group(3) is not None else m.group(4))
            for m in _ATTR_RE.finditer(tag)
        }
        key = (attrs.get("property") or attrs.get("name") or "").lower()
        if key and "content" in attrs and key not in found:
            found[key] = attrs["content"].strip()
    image = (
        found.get("og:image:secure_url")
        or found.get("og:image")
        or found.get("twitter:image")
    )
    if image:
        image = urljoin(base_url, image)
        if image.startswith("http://"):
            image = "https://" + image[len("http://"):]
    title = found.get("og:title") or found.get("twitter:title")
    return image or None, title or None


_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-AU,en;q=0.9",
}


def parse_shopify_js(data: dict, base_url: str) -> tuple[str | None, str | None]:
    """Read a Shopify /products/<handle>.js response."""
    image = data.get("featured_image") or next(iter(data.get("images") or []), None)
    if image:
        if image.startswith("//"):
            image = "https:" + image
        image = urljoin(base_url, image)
    return image or None, data.get("title") or None


async def async_lookup(hass: HomeAssistant, url: str) -> tuple[str | None, str | None]:
    """Fetch a product page and return (image_url, title). Never raises."""
    session = async_get_clientsession(hass)
    timeout = aiohttp.ClientTimeout(total=15)
    page_err: Exception | None = None
    try:
        async with session.get(url, timeout=timeout, headers=_HEADERS) as resp:
            resp.raise_for_status()
            page = await resp.text(errors="replace")
        image, title = parse_meta(page[:500_000], str(resp.url))
        if image:
            return image, title
    except Exception as err:  # noqa: BLE001
        page_err = err
    # Shopify stores (like KegLand) also serve product data at <url>.js,
    # which is sometimes allowed when the HTML page is blocked.
    if "/products/" in url:
        js_url = url.split("?", 1)[0].rstrip("/") + ".js"
        try:
            async with session.get(js_url, timeout=timeout, headers=_HEADERS) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)
            image, title = parse_shopify_js(data, url)
            if image:
                return image, title
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Shopify fallback %s failed: %s", js_url, err)
    _LOGGER.warning(
        "Couldn't get a picture from %s (%s). Paste the image address into the "
        "keg's Image URL option instead.",
        url,
        page_err or "no og:image on the page",
    )
    return None, None
