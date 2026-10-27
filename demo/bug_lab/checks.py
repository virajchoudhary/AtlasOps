"""Objective checks of the Atlas Store, independent of any agent's claims.

Server checks request the live store over HTTP. Browser checks come from the
monitor script that runs inside the store page; only a report for the current
site version counts.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from typing import Any

import httpx

from demo.bug_lab.workspace import MONITOR_TAG

SLOW_API_MS = 1500


@dataclass
class Check:
    name: str
    label: str
    source: str
    ok: bool | None
    detail: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    def public(self) -> dict[str, Any]:
        return asdict(self)


class _Assets(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.stylesheets: list[str] = []
        self.scripts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "link" and (values.get("rel") or "").lower() == "stylesheet" and values.get("href"):
            self.stylesheets.append(values["href"])
        if tag == "script" and values.get("src"):
            self.scripts.append(values["src"])


def _local(url: str) -> bool:
    return not url.startswith(("http://", "https://", "//", "/lab/", "data:"))


async def server_checks(client: httpx.AsyncClient, *, slow_api_ms: int = SLOW_API_MS) -> list[Check]:
    checks: list[Check] = []
    page = await client.get("/shop/")
    checks.append(Check("page_loads", "Store page loads", "server", page.status_code == 200,
                        f"GET /shop/ returned {page.status_code}"))
    html = page.text if page.status_code == 200 else ""
    checks.append(Check("monitor_present", "Monitor script present", "server", MONITOR_TAG in html,
                        "" if MONITOR_TAG in html else "The page no longer loads /lab/monitor.js"))

    assets = _Assets()
    assets.feed(html)
    for kind, label, urls in (
        ("stylesheets_load", "Stylesheets load", assets.stylesheets),
        ("scripts_load", "Scripts load", assets.scripts),
    ):
        failures = []
        for url in filter(_local, urls):
            response = await client.get(f"/shop/{url}")
            if response.status_code != 200:
                failures.append({"url": url, "status": response.status_code})
        detail = "; ".join(f"{item['url']} returned {item['status']}" for item in failures)
        checks.append(Check(kind, label, "server", not failures and bool(html), detail,
                            {"failures": failures, "urls": urls}))

    started = time.monotonic()
    api = await client.get("/shop/api/products")
    elapsed_ms = round((time.monotonic() - started) * 1000)
    products: list[Any] = []
    api_detail = f"GET /shop/api/products returned {api.status_code}"
    api_ok = api.status_code == 200
    if api_ok:
        try:
            data = api.json()
            products = data.get("products") if isinstance(data, dict) else None
            if (
                not isinstance(products, list) or not products
                or not all(isinstance(item, dict) for item in products)
            ):
                products = []
                api_ok, api_detail = False, "The product API requires a nonempty list of product objects"
        except (ValueError, AttributeError):
            products = []
            api_ok, api_detail = False, "The product API did not return JSON"
        if api_ok and not products:
            api_ok, api_detail = False, "The product API returned no products"
    elif api.headers.get("content-type", "").startswith("application/json"):
        try:
            error = api.json()
            api_detail += f": {error.get('error', '') if isinstance(error, dict) else 'Invalid error data'}"
        except ValueError:
            api_detail += ": Invalid error JSON"
    checks.append(Check("products_api", "Product API returns valid data", "server", api_ok,
                        "" if api_ok else api_detail, {"status": api.status_code}))

    bad_prices = [
        {"id": item.get("id"), "price": item.get("price")}
        for item in products
        if not isinstance(item, dict)
        or isinstance(item.get("price"), bool)
        or not isinstance(item.get("price"), (int, float))
        or not math.isfinite(item["price"]) or item["price"] <= 0
    ]
    checks.append(Check(
        "prices_numeric", "Prices are numbers", "server", (not bad_prices) if products else None,
        "; ".join(f"{item['id']}: {json.dumps(item['price'])}" for item in bad_prices[:4]),
        {"bad_prices": bad_prices},
    ))

    missing_images = []
    for item in products:
        image = item.get("image") if isinstance(item, dict) else None
        if not isinstance(image, str) or not _local(image):
            missing_images.append({"id": item.get("id") if isinstance(item, dict) else None, "image": image})
            continue
        if (await client.get(f"/shop/{image}")).status_code != 200:
            missing_images.append({"id": item.get("id"), "image": image})
    checks.append(Check(
        "images_load", "Product images load", "server", (not missing_images) if products else None,
        "; ".join(f"{item['image']} returned 404" for item in missing_images[:3]),
        {"missing": missing_images},
    ))

    checks.append(Check(
        "api_fast", "Product API responds fast", "server", elapsed_ms < slow_api_ms if api_ok else None,
        f"{elapsed_ms} ms (limit {slow_api_ms} ms)", {"elapsed_ms": elapsed_ms, "limit_ms": slow_api_ms},
    ))
    return checks


def browser_checks(report: dict[str, Any] | None, *, slow_api_ms: int = SLOW_API_MS) -> list[Check]:
    if not report:
        return []
    issues = report.get("issues") or []
    observed = report.get("checks") or {}

    def messages(kinds: set[str]) -> list[str]:
        return [str(item.get("message", "")) for item in issues if item.get("kind") in kinds]

    errors = messages({"js_error", "console_error"})
    failed = messages({"resource", "http_error", "network_error"})
    rendered = observed.get("products_rendered")
    nan_prices = observed.get("nan_prices")
    broken = observed.get("broken_images")
    overflow = observed.get("overflow_px")
    cart = observed.get("add_to_cart")
    api_ms = observed.get("api_ms")
    return [
        Check("js_errors", "No JavaScript errors", "browser", not errors, "; ".join(errors[:2]),
              {"messages": errors}),
        Check("requests_ok", "No failed requests", "browser", not failed, "; ".join(failed[:2]),
              {"messages": failed}),
        Check("styles_applied", "Styles applied", "browser", observed.get("stylesheet_applied"),
              "" if observed.get("stylesheet_applied") else "The store stylesheet is not in effect"),
        Check("products_rendered", "Products rendered", "browser",
              rendered > 0 if isinstance(rendered, int) else None, f"{rendered} product cards on the page"),
        Check("prices_display", "Prices display correctly", "browser",
              nan_prices == 0 if isinstance(nan_prices, int) and rendered else None,
              f"{nan_prices} prices show NaN" if nan_prices else ""),
        Check("images_display", "Images display", "browser",
              not broken if isinstance(broken, list) and rendered else None,
              f"{len(broken)} broken images" if broken else ""),
        Check("fits_screen", "Page fits the screen", "browser",
              overflow <= 2 if isinstance(overflow, (int, float)) else None,
              f"Page is {overflow}px wider than the window" if overflow and overflow > 2 else ""),
        Check("add_to_cart", "Add to cart works", "browser",
              {"ok": True, "failed": False}.get(cart), {"failed": "The cart count did not change",
                                                       "skipped": "Not tested: no products"}.get(cart, "")),
        Check("api_fast_browser", "Products load fast", "browser",
              api_ms < slow_api_ms if isinstance(api_ms, (int, float)) else None,
              f"{round(api_ms)} ms in the browser" if isinstance(api_ms, (int, float)) else ""),
    ]


def failing(checks: list[Check]) -> list[Check]:
    return [check for check in checks if check.ok is False]


def all_pass(checks: list[Check]) -> bool:
    return bool(checks) and all(check.ok is True for check in checks)
