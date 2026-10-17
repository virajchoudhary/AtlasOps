import { expect, test } from "@playwright/test";

for (const viewport of [
  { name: "desktop", width: 1280, height: 720 },
  { name: "mobile", width: 390, height: 844 },
]) {
  test(`reel presentation and pointer/keyboard playback on ${viewport.name}`, async ({
    page,
  }, testInfo) => {
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.setViewportSize(viewport);
    await page.addInitScript(() => {
      sessionStorage.setItem("content-architecture-newsletter-dismissed", "1");
    });
    await page.goto("/reference");
    const watch = page.getByRole("button", { name: /watch reel/i });
    await watch.click();

    const dialog = page.getByRole("dialog", { name: "Watch reel", exact: true });
    const reel = page.getByLabel("The Content Architecture product reel", { exact: true });
    const close = dialog.getByRole("button", { name: "Close", exact: true });
    await expect(dialog).toBeVisible();
    await expect(reel).toHaveJSProperty("controls", false);
    await expect(reel).toHaveAttribute("role", "button");
    await expect(reel).toHaveAttribute("tabindex", "0");
    await expect.poll(() => reel.evaluate((video: HTMLVideoElement) => video.currentTime))
      .toBeGreaterThan(0.4);
    await expect(reel).toHaveAttribute("aria-pressed", "true");
    await expect(close).toBeFocused();
    await expect(close).toHaveCSS("font-family", /Geist Mono/);
    await expect(close).toHaveCSS("font-size", "13.5px");
    const inset = viewport.name === "desktop" ? 16 : 8;
    expect(await close.evaluate((element) => {
      const rect = element.getBoundingClientRect();
      return [rect.top, innerWidth - rect.right];
    })).toEqual([inset, inset]);

    expect(await dialog.evaluate((element) => {
      const rect = element.getBoundingClientRect();
      return [rect.x, rect.y, rect.width, rect.height];
    })).toEqual([0, 0, viewport.width, viewport.height]);
    expect(await close.locator("svg").evaluate((element) => {
      const rect = element.getBoundingClientRect();
      return [rect.width, rect.height];
    })).toEqual([48, 48]);

    await reel.click();
    await expect(reel).toHaveJSProperty("paused", true);
    await expect(reel).toHaveAttribute("aria-pressed", "false");
    const pausedTime = await reel.evaluate((video: HTMLVideoElement) => video.currentTime);
    await page.screenshot({
      path: testInfo.outputPath(`reel-pointer-paused-${viewport.name}.png`),
    });
    await reel.press("Space");
    await expect(reel).toHaveJSProperty("paused", false);
    await expect.poll(() => reel.evaluate((video: HTMLVideoElement) => video.currentTime))
      .toBeGreaterThan(pausedTime + 0.2);
    await reel.press("Enter");
    await expect(reel).toHaveJSProperty("paused", true);
    await page.screenshot({ path: testInfo.outputPath(`reel-paused-${viewport.name}.png`) });
    await reel.press("Space");
    await expect(reel).toHaveJSProperty("paused", false);
    await close.click();
    await expect(dialog).toBeHidden();
    await expect(watch).toBeFocused();

    await watch.click();
    await expect(dialog).toBeVisible();
    await expect.poll(() => reel.evaluate((video: HTMLVideoElement) => video.currentTime))
      .toBeGreaterThan(0.2);
    await close.press("Escape");
    await expect(dialog).toBeHidden();
    await expect(watch).toBeFocused();
    expect(errors).toEqual([]);
  });
}

test("reel keeps native playback available when autoplay is rejected", async ({ page }) => {
  await page.addInitScript(() => {
    sessionStorage.setItem("content-architecture-newsletter-dismissed", "1");
    const nativePlay = HTMLMediaElement.prototype.play;
    let blockNextPlay = true;
    HTMLMediaElement.prototype.play = function () {
      if (!blockNextPlay) return nativePlay.call(this);
      blockNextPlay = false;
      this.autoplay = false;
      this.pause();
      return Promise.reject(new DOMException("Autoplay blocked", "NotAllowedError"));
    };
  });
  await page.goto("/reference");
  await page.getByRole("button", { name: /watch reel/i }).click();
  const reel = page.getByLabel("The Content Architecture product reel", { exact: true });
  await expect(reel).toHaveJSProperty("controls", true);
  await expect(reel).toHaveJSProperty("paused", true);
  await expect(page.getByRole("button", { name: "Close", exact: true })).toBeFocused();
  await reel.press("Space");
  await expect.poll(() => reel.evaluate((video: HTMLVideoElement) => video.currentTime))
    .toBeGreaterThan(0.4);
  await expect(reel).toHaveJSProperty("paused", false);
  await expect(reel).toHaveJSProperty("controls", false);
  await reel.press("Enter");
  await expect(reel).toHaveJSProperty("paused", true);
});
