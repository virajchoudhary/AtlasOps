// AtlasOps in-page monitor for the Atlas Store. It is served by the lab, not by
// the store, so a broken store file cannot break the sensor.
(function () {
  "use strict";

  const issues = [];
  const loadId = Math.random().toString(36).slice(2, 10);
  const page = window.top === window ? "tab" : "preview";
  const originalFetch = window.fetch.bind(window);
  const originalConsoleError = console.error.bind(console);
  let loadedVersion = null;
  let apiMs = null;

  function shortUrl(url) {
    return String(url || "").replace(location.origin, "");
  }

  function describe(value) {
    if (value instanceof Error) return value.name + ": " + value.message;
    if (typeof value === "string") return value;
    try { return JSON.stringify(value); } catch (_) { return String(value); }
  }

  function push(kind, message, source, line) {
    if (issues.length >= 40) return;
    issues.push({ kind: kind, message: String(message).slice(0, 300), source: shortUrl(source), line: line ? String(line) : "" });
  }

  window.addEventListener("error", function (event) {
    const target = event.target;
    if (target && target !== window && target.tagName) {
      const url = target.src || target.href || "";
      if (url.indexOf("/lab/") === -1) push("resource", target.tagName.toLowerCase() + " failed to load: " + shortUrl(url));
      return;
    }
    const error = event.error;
    push("js_error", error && error.name ? describe(error) : event.message, event.filename, event.lineno);
  }, true);

  window.addEventListener("unhandledrejection", function (event) {
    push("js_error", describe(event.reason));
  });

  console.error = function () {
    push("console_error", Array.prototype.map.call(arguments, describe).join(" "));
    return originalConsoleError.apply(console, arguments);
  };

  window.fetch = async function (input, init) {
    const url = typeof input === "string" ? input : input.url;
    const started = performance.now();
    try {
      const response = await originalFetch(input, init);
      if (/api\/products/.test(url)) apiMs = Math.round(performance.now() - started);
      if (!response.ok) push("http_error", "GET " + shortUrl(url) + " returned " + response.status);
      return response;
    } catch (error) {
      push("network_error", "GET " + shortUrl(url) + " failed: " + describe(error));
      throw error;
    }
  };

  function sleep(ms) {
    return new Promise(function (resolve) { setTimeout(resolve, ms); });
  }

  async function siteVersion() {
    const response = await originalFetch("/lab/api/site-version", { cache: "no-store" });
    return (await response.json()).version;
  }

  // Ask for the version immediately, before the store's own files load.
  const versionAtLoad = siteVersion();

  async function waitForProducts(timeoutMs) {
    const started = Date.now();
    for (;;) {
      const cards = document.querySelectorAll("#product-grid .card").length;
      const status = document.getElementById("status");
      const failed = status && /could not/i.test(status.textContent);
      if (cards > 0 || failed || Date.now() - started > timeoutMs) return cards;
      await sleep(150);
    }
  }

  async function waitForImages(timeoutMs) {
    const started = Date.now();
    while (Date.now() - started < timeoutMs) {
      const images = Array.prototype.slice.call(document.querySelectorAll("#product-grid img"));
      if (images.every(function (image) { return image.complete; })) return;
      await sleep(100);
    }
  }

  async function addToCartCheck() {
    const button = document.querySelector("#product-grid .add");
    const counter = document.getElementById("cart-count");
    if (!button) return "skipped";
    const before = counter ? counter.textContent : null;
    button.click();
    await sleep(250);
    return counter && counter.textContent !== before ? "ok" : "failed";
  }

  function badge(problems) {
    let element = document.getElementById("atlasops-monitor-badge");
    if (!element) {
      element = document.createElement("div");
      element.id = "atlasops-monitor-badge";
      Object.assign(element.style, {
        position: "fixed", left: "12px", bottom: "12px", zIndex: "2147483647", padding: "6px 10px",
        borderRadius: "999px", font: "12px/1.3 Helvetica, Arial, sans-serif", color: "#ffffff",
        boxShadow: "0 2px 8px rgba(0,0,0,0.25)", pointerEvents: "none",
      });
      document.body.appendChild(element);
    }
    element.style.background = problems ? "#b3261e" : "#1e7a46";
    element.textContent = problems
      ? "AtlasOps monitor: " + problems + (problems === 1 ? " problem" : " problems") + " seen"
      : "AtlasOps monitor: all checks pass";
  }

  async function runChecks() {
    loadedVersion = await versionAtLoad;
    const cards = await waitForProducts(9000);
    await waitForImages(3000);
    const prices = Array.prototype.slice.call(document.querySelectorAll("#product-grid .price"));
    const images = Array.prototype.slice.call(document.querySelectorAll("#product-grid img"));
    const checks = {
      stylesheet_applied: getComputedStyle(document.documentElement).getPropertyValue("--atlas-stylesheet").trim() === "loaded",
      products_rendered: cards,
      nan_prices: prices.filter(function (price) { return /NaN/.test(price.textContent); }).length,
      broken_images: images.filter(function (image) { return image.complete && image.naturalWidth === 0; })
        .map(function (image) { return image.getAttribute("src"); }),
      overflow_px: Math.max(0, document.documentElement.scrollWidth - document.documentElement.clientWidth),
      add_to_cart: await addToCartCheck(),
      api_ms: apiMs,
    };
    if ((await siteVersion()) !== loadedVersion) {
      location.reload();
      return;
    }
    await originalFetch("/lab/api/telemetry", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Bug-Lab": "1" },
      body: JSON.stringify({ version: loadedVersion, load_id: loadId, page: page, issues: issues, checks: checks }),
    });
    const problems = issues.length + (checks.stylesheet_applied ? 0 : 1) + (cards ? 0 : 1) + (checks.nan_prices ? 1 : 0)
      + (checks.broken_images.length ? 1 : 0) + (checks.overflow_px > 2 ? 1 : 0) + (checks.add_to_cart === "ok" ? 0 : 1)
      + (apiMs !== null && apiMs >= 1500 ? 1 : 0);
    badge(problems);
  }

  async function watchVersion() {
    for (;;) {
      await sleep(1500);
      try {
        const version = await siteVersion();
        if (loadedVersion && version !== loadedVersion) {
          location.reload();
          return;
        }
      } catch (_) {
        // The lab server may be restarting; keep watching.
      }
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    runChecks().catch(function (error) { originalConsoleError("AtlasOps monitor failed", error); });
    watchVersion();
  });
})();
