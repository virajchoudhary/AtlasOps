import { expect, test } from "@playwright/test";

type PixelSummary = {
  nonblankPixels: number;
  checksum: number;
  horizontalEdgePixels: number;
  verticalEdgePixels: number;
};

async function getVisiblePixelSummary(page: import("@playwright/test").Page): Promise<PixelSummary> {
  return page.locator("canvas.ring-canvas__webgl").evaluate((element) => {
    if (!(element instanceof HTMLCanvasElement)) {
      return {
        nonblankPixels: 0,
        checksum: 0,
        horizontalEdgePixels: 0,
        verticalEdgePixels: 0,
      };
    }
    const gl = element.getContext("webgl2");
    if (!gl) {
      return {
        nonblankPixels: 0,
        checksum: 0,
        horizontalEdgePixels: 0,
        verticalEdgePixels: 0,
      };
    }

    const pixels = new Uint8Array(element.width * element.height * 4);
    gl.readPixels(0, 0, element.width, element.height, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
    let nonblankPixels = 0;
    let horizontalEdgePixels = 0;
    let verticalEdgePixels = 0;
    let checksum = 2166136261;
    const edgeBand = Math.min(16, Math.floor(Math.min(element.width, element.height) / 4));

    for (let y = 0; y < element.height; y += 1) {
      for (let x = 0; x < element.width; x += 1) {
        const index = (y * element.width + x) * 4;
        const red = pixels[index] ?? 0;
        const green = pixels[index + 1] ?? 0;
        const blue = pixels[index + 2] ?? 0;
        if (red > 0 || green > 0 || blue > 0) {
          nonblankPixels += 1;
          if (x < edgeBand || x >= element.width - edgeBand) horizontalEdgePixels += 1;
          if (y < edgeBand || y >= element.height - edgeBand) verticalEdgePixels += 1;
        }
        checksum = Math.imul(checksum ^ red, 16777619) >>> 0;
        checksum = Math.imul(checksum ^ green, 16777619) >>> 0;
        checksum = Math.imul(checksum ^ blue, 16777619) >>> 0;
      }
    }

    return { nonblankPixels, checksum, horizontalEdgePixels, verticalEdgePixels };
  });
}

async function preserveWebGlPixels(page: import("@playwright/test").Page): Promise<void> {
  await page.addInitScript(() => {
    const originalGetContext = HTMLCanvasElement.prototype.getContext;
    Object.defineProperty(HTMLCanvasElement.prototype, "getContext", {
      configurable: true,
      value: function (this: HTMLCanvasElement, contextId: string, ...args: unknown[]) {
        if (contextId === "webgl2") {
          const options = args[0];
          const readbackOptions = typeof options === "object" && options !== null
            ? Object.assign({}, options, { preserveDrawingBuffer: true })
            : { preserveDrawingBuffer: true };
          return Reflect.apply(originalGetContext, this, [contextId, readbackOptions]);
        }
        return Reflect.apply(originalGetContext, this, [contextId, ...args]);
      },
    });
  });
}

test("renders visible moving rings and retains matching desktop/mobile captures", async ({ page }, testInfo) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  await preserveWebGlPixels(page);
  await page.goto("/fixture.html");

  const ringField = page.getByRole("img", { name: "Thirty rotating typographic rings" });
  expect(page.viewportSize()).toEqual({ width: 1440, height: 900 });
  await expect(ringField).toHaveAttribute("data-ring-count", "30");
  await expect(ringField).toHaveAttribute("data-renderer", "webgl2", { timeout: 15_000 });
  await expect(ringField).toHaveAttribute("data-animation-state", "running", { timeout: 10_000 });

  await page.waitForTimeout(180);
  const firstFramePixels = await getVisiblePixelSummary(page);
  expect(firstFramePixels.nonblankPixels).toBeGreaterThan(0);
  expect(firstFramePixels.horizontalEdgePixels).toBeGreaterThan(0);
  expect(firstFramePixels.verticalEdgePixels).toBeGreaterThan(0);

  await page.waitForTimeout(200);
  const secondFramePixels = await getVisiblePixelSummary(page);
  expect(secondFramePixels.nonblankPixels).toBeGreaterThan(0);
  expect(secondFramePixels.horizontalEdgePixels).toBeGreaterThan(0);
  expect(secondFramePixels.verticalEdgePixels).toBeGreaterThan(0);
  expect(secondFramePixels.checksum).not.toBe(firstFramePixels.checksum);

  const desktopDpr = await page.evaluate(() => String(Math.min(window.devicePixelRatio, 2)));
  await expect(ringField).toHaveAttribute("data-pixel-ratio", desktopDpr);
  await page.screenshot({ path: testInfo.outputPath("desktop-rings.png") });

  await page.setViewportSize({ width: 390, height: 844 });
  expect(page.viewportSize()).toEqual({ width: 390, height: 844 });
  const mobileDpr = await page.evaluate(() => String(Math.min(window.devicePixelRatio, 2)));
  await ringField.scrollIntoViewIfNeeded();
  await expect(ringField).toHaveAttribute("data-animation-state", "running", { timeout: 10_000 });
  await expect(ringField).toHaveAttribute("data-pixel-ratio", mobileDpr);
  await expect(ringField).toBeVisible();
  await expect.poll(() =>
    page.locator("canvas.ring-canvas__webgl").evaluate((canvas) =>
      canvas instanceof HTMLCanvasElement ? canvas.width : 0),
  ).toBe(390 * Number(mobileDpr));
  await page.locator(".fixture").evaluate((element) => {
    element.scrollTop = 0;
  });
  await expect(ringField).toHaveAttribute("data-animation-state", "running", { timeout: 10_000 });
  await page.waitForTimeout(180);
  const mobilePixels = await getVisiblePixelSummary(page);
  expect(mobilePixels.horizontalEdgePixels).toBeGreaterThan(0);
  expect(mobilePixels.verticalEdgePixels).toBeGreaterThan(0);
  await page.screenshot({ path: testInfo.outputPath("mobile-rings.png") });
  expect(pageErrors).toEqual([]);
});

test("maps every initial phrase glyph to its own flipped GPU atlas tile", async ({ page }) => {
  await page.goto("/glyph-atlas-fixture.html");

  await expect(page.locator("#glyph-atlas-identity")).toHaveAttribute(
    "data-glyph-identities",
    "T:1|H:2|E:3|_:4|C:5|O:6|N:7|A:8|R:9|I:10|U:11|.:12",
    { timeout: 10_000 },
  );
});

test("preserves top-to-bottom orientation for atlas cells and rendered font glyphs", async ({ page }, testInfo) => {
  await page.goto("/glyph-atlas-fixture.html");
  const orientation = page.locator("#glyph-atlas-orientation");
  await expect(orientation).toHaveAttribute("data-orientation-state", /^(ready|error)$/, {
    timeout: 10_000,
  });
  const metrics = await orientation.evaluate((element) => ({
    error: element.getAttribute("data-error"),
    sentinelTopAlpha: Number(element.getAttribute("data-sentinel-top-alpha")),
    sentinelBottomAlpha: Number(element.getAttribute("data-sentinel-bottom-alpha")),
    glyphPixels: Number(element.getAttribute("data-glyph-pixels")),
    glyphDirectMismatches: Number(element.getAttribute("data-glyph-direct-mismatches")),
    glyphMirroredMismatches: Number(element.getAttribute("data-glyph-mirrored-mismatches")),
  }));
  await testInfo.attach("glyph-orientation-metrics.json", {
    body: JSON.stringify(metrics, null, 2),
    contentType: "application/json",
  });

  expect(metrics.error).toBeNull();
  expect(metrics.glyphPixels).toBeGreaterThan(0);
  expect(
    metrics.glyphDirectMismatches,
    `GPU orientation metrics: ${JSON.stringify(metrics)}`,
  ).toBeLessThanOrEqual(32);
  expect(metrics.glyphDirectMismatches).toBeLessThan(metrics.glyphMirroredMismatches);
  expect(metrics.sentinelTopAlpha).toBeGreaterThan(metrics.sentinelBottomAlpha);
});

test("stops drawing outside the viewport and resumes only after it re-enters", async ({ page }) => {
  await page.goto("/fixture.html?offscreen");

  const ringField = page.getByRole("img", { name: "Rotating typographic rings" });
  await expect(ringField).toHaveAttribute("data-renderer", "pending");
  await page.waitForTimeout(250);
  await expect(ringField).toHaveAttribute("data-renderer", "pending");

  await page.evaluate(() => window.scrollTo(0, window.innerHeight * 0.8));
  const preloadBounds = await ringField.evaluate((element) => {
    const bounds = element.getBoundingClientRect();
    return { top: bounds.top, viewportHeight: window.innerHeight };
  });
  expect(preloadBounds.top).toBeGreaterThan(preloadBounds.viewportHeight);
  expect(preloadBounds.top).toBeLessThan(preloadBounds.viewportHeight + 400);
  const isInsidePreloadMargin = await ringField.evaluate((element) =>
    new Promise<boolean>((resolve) => {
      const observer = new IntersectionObserver((entries) => {
        observer.disconnect();
        resolve(entries.some((entry) => entry.isIntersecting));
      }, { rootMargin: "400px" });
      observer.observe(element);
    }),
  );
  expect(isInsidePreloadMargin).toBe(true);
  await expect(ringField).toHaveAttribute("data-renderer", "webgl2", { timeout: 15_000 });
  await expect(ringField).toHaveAttribute("data-animation-state", "offscreen");
  const offscreenFrame = await ringField.getAttribute("data-frame");
  await page.waitForTimeout(200);
  await expect(ringField).toHaveAttribute("data-frame", offscreenFrame ?? "");

  await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
  await expect(ringField).toHaveAttribute("data-animation-state", "running", { timeout: 10_000 });
  await expect.poll(() => ringField.getAttribute("data-frame")).not.toBe(offscreenFrame);
});

test("rebuilds the WebGL surface when configurable text changes", async ({ page }) => {
  await preserveWebGlPixels(page);
  await page.goto("/fixture.html");
  const ringField = page.getByRole("img", { name: "Thirty rotating typographic rings" });
  await expect(ringField).toHaveAttribute("data-renderer", "webgl2", { timeout: 15_000 });
  await page.waitForTimeout(100);
  const originalPixels = await getVisiblePixelSummary(page);
  const originalCanvas = await page.locator("canvas.ring-canvas__webgl").elementHandle();
  if (!originalCanvas) throw new Error("Ring canvas did not mount.");

  await page.getByRole("button", { name: "Change ring text" }).click();
  await expect(ringField).toHaveAttribute("data-renderer", "webgl2", { timeout: 15_000 });
  await expect(ringField).toHaveAttribute("data-animation-state", "running", { timeout: 10_000 });
  await expect.poll(() => originalCanvas.evaluate((canvas) => canvas.isConnected)).toBe(false);
  await page.waitForTimeout(100);
  const updatedPixels = await getVisiblePixelSummary(page);

  expect(updatedPixels.nonblankPixels).toBeGreaterThan(0);
  expect(updatedPixels.checksum).not.toBe(originalPixels.checksum);
  await originalCanvas.dispose();
});

test("releases its WebGL context when unmounted", async ({ page }) => {
  await page.goto("/fixture.html");
  const ringField = page.getByRole("img", { name: "Thirty rotating typographic rings" });
  await expect(ringField).toHaveAttribute("data-renderer", "webgl2", { timeout: 15_000 });
  const canvas = await page.locator("canvas.ring-canvas__webgl").elementHandle();
  if (!canvas) throw new Error("Ring canvas did not mount.");
  expect(await canvas.evaluate((element) =>
    element instanceof HTMLCanvasElement && element.getContext("webgl2") !== null,
  )).toBe(true);

  await page.getByRole("button", { name: "Toggle rings" }).click();
  await expect(ringField).toHaveCount(0);
  await expect.poll(() => canvas.evaluate((element) =>
    element instanceof HTMLCanvasElement && element.getContext("webgl2")?.isContextLost() === true,
  )).toBe(true);
  await canvas.dispose();
});

test("falls back to 2D after WebGL context loss", async ({ page }) => {
  await page.goto("/fixture.html");
  const ringField = page.getByRole("img", { name: "Thirty rotating typographic rings" });
  await expect(ringField).toHaveAttribute("data-renderer", "webgl2", { timeout: 15_000 });
  const contextLossSupported = await page.locator("canvas.ring-canvas__webgl").evaluate((element) => {
    if (!(element instanceof HTMLCanvasElement)) return false;
    const context = element.getContext("webgl2");
    const extension = context?.getExtension("WEBGL_lose_context");
    if (!extension) return false;
    extension.loseContext();
    return true;
  });
  expect(contextLossSupported).toBe(true);
  await expect(ringField).toHaveAttribute("data-renderer", "2d", { timeout: 10_000 });
  await expect(ringField).toHaveAttribute("data-animation-state", "fallback");
  await expect(ringField.locator("canvas.ring-canvas__fallback")).toBeVisible();
});

test("draws one static frame for reduced motion, then resumes on preference change", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/fixture.html");

  const ringField = page.getByRole("img", { name: "Thirty rotating typographic rings" });
  await expect(ringField).toHaveAttribute("data-renderer", "webgl2", { timeout: 15_000 });
  await expect(ringField).toHaveAttribute("data-animation-state", "reduced-motion");
  const staticFrame = await ringField.getAttribute("data-frame");
  await page.waitForTimeout(250);
  await expect(ringField).toHaveAttribute("data-frame", staticFrame ?? "");

  await page.emulateMedia({ reducedMotion: "no-preference" });
  await expect(ringField).toHaveAttribute("data-animation-state", "running", { timeout: 10_000 });
  await expect.poll(() => ringField.getAttribute("data-frame")).not.toBe(staticFrame);
});

test("caps the rendering pixel ratio at 2 on a high-DPR display", async ({ browser }) => {
  const context = await browser.newContext({
    viewport: { width: 900, height: 700 },
    deviceScaleFactor: 3,
  });
  const page = await context.newPage();
  await page.goto("http://127.0.0.1:7866/fixture.html");
  const ringField = page.getByRole("img", { name: "Thirty rotating typographic rings" });

  await expect(ringField).toHaveAttribute("data-renderer", "webgl2", { timeout: 15_000 });
  await expect(ringField).toHaveAttribute("data-pixel-ratio", "2");
  await context.close();
});

test("falls back to its accessible 2D renderer when WebGL2 is unavailable", async ({ page }) => {
  await page.addInitScript(() => {
    const originalGetContext = HTMLCanvasElement.prototype.getContext;
    Object.defineProperty(HTMLCanvasElement.prototype, "getContext", {
      configurable: true,
      value: function (this: HTMLCanvasElement, contextId: string, ...args: unknown[]) {
        if (contextId === "webgl2") return null;
        return Reflect.apply(originalGetContext, this, [contextId, ...args]);
      },
    });
  });
  await page.goto("/fixture.html");

  const ringField = page.getByRole("img", { name: "Thirty rotating typographic rings" });
  await expect(ringField).toHaveAttribute("data-renderer", "2d", { timeout: 15_000 });
  await expect(ringField).toHaveAttribute("data-animation-state", "fallback");
  await expect(ringField.locator("canvas.ring-canvas__fallback")).toBeVisible();

  const fallbackHasVisiblePixels = await ringField.locator("canvas.ring-canvas__fallback")
    .evaluate((element) => {
      if (!(element instanceof HTMLCanvasElement)) return false;
      const context = element.getContext("2d");
      if (!context) return false;
      const pixels = context.getImageData(0, 0, element.width, element.height).data;

      for (let index = 3; index < pixels.length; index += 4) {
        if ((pixels[index] ?? 0) > 0) return true;
      }

      return false;
    });
  expect(fallbackHasVisiblePixels).toBe(true);
});
