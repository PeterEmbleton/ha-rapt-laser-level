"""Look up a drink's picture (and name) from a product page URL."""

from __future__ import annotations

import html
import logging
import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

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


_TRACKING_PREFIXES = ("utm_", "pr_", "_pos", "_sid", "_ss", "fbclid", "gclid", "srsltid")


def clean_product_url(url: str) -> str:
    """Drop tracking parameters (e.g. KegLand's pr_prod_strat=...) from a link."""
    parts = urlsplit(url)
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith(_TRACKING_PREFIXES)
    ]
    return urlunsplit(parts._replace(query=urlencode(query), fragment=""))


def shopify_js_url(url: str) -> str | None:
    """Return the Shopify product feed URL for a /products/<handle> link."""
    parts = urlsplit(url)
    if "/products/" not in parts.path:
        return None
    path = parts.path.rstrip("/")
    if not path.endswith(".js"):
        path += ".js"
    return urlunsplit(parts._replace(path=path, query="", fragment=""))


class _RateLimited(Exception):
    """The site answered 429; stop asking."""


async def _get(session: aiohttp.ClientSession, url: str, as_json: bool):
    async with session.get(
        url, timeout=aiohttp.ClientTimeout(total=15), headers=_HEADERS
    ) as resp:
        if resp.status == 429:
            raise _RateLimited(url)
        resp.raise_for_status()
        if as_json:
            return await resp.json(content_type=None), str(resp.url)
        return await resp.text(errors="replace"), str(resp.url)


async def async_lookup(hass: HomeAssistant, url: str) -> tuple[str | None, str | None]:
    """Find a product's (image_url, title). Never raises.

    Shopify links (KegLand) use the small /products/<handle>.js feed first:
    one request, and its picture lives on cdn.shopify.com, so showing it
    never touches the shop's own site. Other links read the page's og:image.
    At most two requests, and none after a 429.
    """
    session = async_get_clientsession(hass)
    url = clean_product_url(url)
    err: Exception | None = None
    try:
        if js_url := shopify_js_url(url):
            try:
                data, _ = await _get(session, js_url, as_json=True)
                image, title = parse_shopify_js(data, url)
                if image:
                    return image, title
            except _RateLimited:
                raise
            except Exception as js_err:  # noqa: BLE001
                _LOGGER.debug("Shopify feed %s failed: %s", js_url, js_err)
        page, final_url = await _get(session, url, as_json=False)
        image, title = parse_meta(page[:500_000], final_url)
        if image:
            return image, title
    except _RateLimited:
        err = Exception("429 Too Many Requests; not retrying until the next restart")
    except Exception as page_err:  # noqa: BLE001
        err = page_err
    _LOGGER.warning(
        "Couldn't get a picture from %s (%s). Paste the image address into the "
        "keg's Image URL option instead.",
        url,
        err or "no og:image on the page",
    )
    return None, None
