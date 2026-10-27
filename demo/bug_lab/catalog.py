"""Intentional bugs the lab can inject into the Atlas Store working copy.

Each bug is a small, deterministic edit to exactly one store file. The agents
never receive the bug id or its description: they must find the fault from
what the monitor and the verifier observe.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Bug:
    id: str
    name: str
    kind: str
    file: str
    summary: str
    symptom: str
    mutate: Callable[[str], str]

    def public(self) -> dict[str, str]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "file": self.file,
            "summary": self.summary,
            "symptom": self.symptom,
        }


def _replace_once(old: str, new: str) -> Callable[[str], str]:
    def mutate(text: str) -> str:
        if text.count(old) != 1:
            raise ValueError(f"Bug anchor not found exactly once: {old!r}")
        return text.replace(old, new)

    return mutate


def _truncate_json(text: str) -> str:
    # A deploy that cut the file short: the closing brackets are gone and a
    # dangling comma is left after the last product.
    ending = "\n  ]\n}\n"
    if not text.endswith(ending):
        raise ValueError("Product data does not have the expected ending")
    return text[: -len(ending)] + ",\n"


def _string_prices(text: str) -> str:
    pattern = re.compile(r'"price": (\d+(?:\.\d+)?)')
    if not pattern.search(text):
        raise ValueError("No numeric prices to corrupt")
    return pattern.sub(lambda match: f'"price": "{float(match.group(1)):.2f} USD"', text)


def _move_images(text: str) -> str:
    if '"image": "images/' not in text:
        raise ValueError("No image paths to break")
    return text.replace('"image": "images/', '"image": "img/')


BUGS: tuple[Bug, ...] = (
    Bug(
        id="js-reference-error",
        name="JavaScript crash",
        kind="Runtime error",
        file="app.js",
        summary="A typo in a function call throws a ReferenceError while the page renders.",
        symptom="The product grid stays empty and the status line says products could not load.",
        mutate=_replace_once("  renderProducts(state.products);", "  renderProdcuts(state.products);"),
    ),
    Bug(
        id="broken-stylesheet",
        name="Broken stylesheet link",
        kind="Missing asset",
        file="index.html",
        summary="The page links a stylesheet file that does not exist.",
        symptom="The store loses all styling and renders as plain HTML.",
        mutate=_replace_once('href="styles.css"', 'href="style.css"'),
    ),
    Bug(
        id="truncated-data",
        name="Truncated product data",
        kind="Data / API failure",
        file="products.json",
        summary="The product file is cut off, so the product API returns HTTP 500.",
        symptom="No products load; the status line reports a failure.",
        mutate=_truncate_json,
    ),
    Bug(
        id="string-prices",
        name="Prices show NaN",
        kind="Data type error",
        file="products.json",
        summary="Prices are stored as text like \"18.50 USD\" instead of numbers.",
        symptom="Every product shows the price $NaN.",
        mutate=_string_prices,
    ),
    Bug(
        id="broken-image-paths",
        name="Broken image paths",
        kind="Asset path",
        file="products.json",
        summary="Product images point at a folder that does not exist.",
        symptom="Every product card shows a broken image.",
        mutate=_move_images,
    ),
    Bug(
        id="cart-handler",
        name="Add to cart does nothing",
        kind="Functional regression",
        file="app.js",
        summary="The cart handler looks up an element id with a typo.",
        symptom="Clicking Add to cart throws an error and the cart count never changes.",
        mutate=_replace_once('getElementById("cart-count")', 'getElementById("cart-cuont")'),
    ),
    Bug(
        id="layout-overflow",
        name="Layout overflows the screen",
        kind="CSS layout",
        file="styles.css",
        summary="The product grid gets a fixed width far wider than any screen.",
        symptom="The page scrolls sideways and most products are off screen.",
        mutate=_replace_once("  width: 100%;\n  grid-template-columns", "  width: 2600px;\n  grid-template-columns"),
    ),
    Bug(
        id="slow-api",
        name="Slow product API",
        kind="Performance",
        file="config.json",
        summary="A configuration change adds a four-second delay to every product request.",
        symptom="Products appear only after a long wait.",
        mutate=_replace_once('"api_delay_ms": 0', '"api_delay_ms": 4000'),
    ),
)

BUGS_BY_ID = {bug.id: bug for bug in BUGS}
