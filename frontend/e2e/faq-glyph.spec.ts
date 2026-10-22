import { expect, test } from "@playwright/test";

for (const viewport of [{ width: 1904, height: 881 }, { width: 390, height: 844 }]) {
  test(`FAQ glyph backdrop renders and preserves controls at ${viewport.width}`, async ({ page }, testInfo) => {
    await page.setViewportSize(viewport);
    await page.goto("/reference");
    await page.evaluate(() => document.fonts.ready);
    const faq = page.locator("#faq");
    await faq.scrollIntoViewIfNeeded();
    const canvas = faq.locator(".reference-faq-glyphs canvas");
    await expect(canvas).toBeVisible();
    await expect(faq.locator(".reference-faq-glyphs")).toHaveAttribute("aria-hidden", "true");
    await expect(canvas).toHaveAttribute("data-animation-state", "running");

    const pixels = await canvas.evaluate((element) => {
      if (!(element instanceof HTMLCanvasElement)) throw new Error("Missing glyph canvas");
      const context = element.getContext("2d");
      if (!context) throw new Error("Missing glyph raster");
      const image = context.getImageData(0, 0, element.width, element.height);
      let lit = 0;
      for (let i = 0; i < image.data.length; i += 4) {
        if (image.data[i + 3] > 0 && image.data[i] > 40) lit += 1;
      }
      return { width: element.width, height: element.height, lit };
    });
    expect(pixels.width).toBeGreaterThan(300);
    expect(pixels.height).toBeGreaterThan(500);
    expect(pixels.lit).toBeGreaterThan(100);

    const before = await canvas.getAttribute("data-frame");
    await expect.poll(() => canvas.getAttribute("data-frame")).not.toBe(before);
    const raster = await canvas.evaluate((element) => {
      if (!(element instanceof HTMLCanvasElement)) throw new Error("Missing glyph canvas");
      return element.toDataURL();
    });
    await expect.poll(() => canvas.evaluate((element) => {
      if (!(element instanceof HTMLCanvasElement)) throw new Error("Missing glyph canvas");
      return element.toDataURL();
    })).not.toBe(raster);
    await page.screenshot({ path: testInfo.outputPath("faq-glyph.png") });
    const triggers = faq.locator(".reference-faq-trigger");
    await expect(triggers.first()).toHaveAttribute("aria-expanded", "true");
    await triggers.nth(1).click();
    await expect(triggers.nth(1)).toHaveAttribute("aria-expanded", "true");
    await expect(triggers.first()).toHaveAttribute("aria-expanded", "false");
    await triggers.nth(1).press("Space");
    await expect(triggers.nth(1)).toHaveAttribute("aria-expanded", "false");
    await expect(page.locator("html")).toHaveJSProperty("scrollWidth", await page.evaluate(() => document.documentElement.clientWidth));

    await page.evaluate(() => window.scrollTo(0, 0));
    await expect(canvas).toHaveAttribute("data-animation-state", "paused");
    const paused = await canvas.getAttribute("data-frame");
    await page.waitForTimeout(500);
    expect(await canvas.getAttribute("data-frame")).toBe(paused);
    await faq.scrollIntoViewIfNeeded();
    await expect(canvas).toHaveAttribute("data-animation-state", "running");
    const scrollBefore = await page.evaluate(() => window.scrollY);
    await page.evaluate(() => window.scrollBy(0, 7));
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(scrollBefore + 7);
    const alignment = await canvas.evaluate((element) => {
      const section = element.closest("section");
      if (!section) throw new Error("Missing FAQ section");
      const travel = Math.max(0, -section.getBoundingClientRect().top);
      const matrix = new DOMMatrixReadOnly(getComputedStyle(element).transform);
      return Math.abs(matrix.m42 - (Math.floor(travel / 14) * 14 - travel));
    });
    expect(alignment).toBeLessThan(0.1);
  });
}

test("FAQ glyph backdrop stays static under reduced motion and resumes", async ({ page }, testInfo) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/reference");
  await page.evaluate(() => document.fonts.ready);
  await page.locator("#faq").scrollIntoViewIfNeeded();
  const canvas = page.locator("#faq .reference-faq-glyphs canvas");
  await expect(canvas).toBeVisible();
  await expect(canvas).toHaveAttribute("data-animation-state", "reduced-motion");
  // The state is set before the glyph font loads; sample only after the first static paint.
  await expect.poll(() => canvas.getAttribute("data-frame")).not.toBe("0");
  const frame = await canvas.getAttribute("data-frame");
  await page.waitForTimeout(500);
  expect(await canvas.getAttribute("data-frame")).toBe(frame);
  await page.screenshot({ path: testInfo.outputPath("faq-reduced-motion.png") });
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await expect(canvas).toHaveAttribute("data-animation-state", "running");
  await expect.poll(() => canvas.getAttribute("data-frame")).not.toBe(frame);
});

test("FAQ remains usable when its glyph font is unavailable", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/reference/assets/GeistMono-VF.woff2", (route) => route.abort());
  await page.goto("/reference");
  await page.locator("#faq").scrollIntoViewIfNeeded();
  const canvas = page.locator("#faq .reference-faq-glyphs canvas");
  await expect(canvas).toHaveAttribute("data-animation-state", "running");
  await expect.poll(() => canvas.getAttribute("data-frame")).not.toBe("0");
  const visibleGlyphs = await canvas.evaluate((element) => {
    if (!(element instanceof HTMLCanvasElement)) throw new Error("Missing glyph canvas");
    const context = element.getContext("2d");
    if (!context) throw new Error("Missing glyph raster");
    const pixels = context.getImageData(0, 0, element.width, element.height).data;
    let lit = 0;
    for (let i = 0; i < pixels.length; i += 4) if (pixels[i] > 40) lit += 1;
    return lit;
  });
  expect(visibleGlyphs).toBeGreaterThan(100);
  await page.locator("#faq .reference-faq-trigger").nth(1).click();
  await expect(page.locator("#faq .reference-faq-trigger").nth(1)).toHaveAttribute("aria-expanded", "true");
  expect(errors).toEqual([]);
});
