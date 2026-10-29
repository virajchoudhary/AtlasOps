import { expect, test } from "@playwright/test";

for (const viewport of [
  { name: "desktop", width: 1280, height: 720 },
  { name: "mobile", width: 390, height: 844 },
]) {
  test(`founder controls operate locally on ${viewport.name}`, async ({ page }, testInfo) => {
    test.slow();
    await page.setViewportSize(viewport);
    await page.addInitScript(() => {
      sessionStorage.setItem("content-architecture-newsletter-dismissed", "1");
    });
    await page.goto("/reference");
    const trigger = page.getByRole("button", { name: /learn more/i });
    await trigger.click();
    const controller = page.locator(".reference-about-video media-controller");
    const video = page.getByLabel("Edoardo Lunardi profile video");
    await video.scrollIntoViewIfNeeded();
    await expect(controller).toBeVisible();
    await expect(video).toHaveJSProperty("controls", false);
    await expect.poll(() =>
      video.evaluate((element: HTMLVideoElement) => element.currentTime),
    ).toBeGreaterThan(0.4);
    await controller.hover();

    const bottomPlay = controller.locator(".reference-founder-play");
    const centerPlay = controller.locator(".reference-founder-center-play");
    await expect(bottomPlay).toBeVisible();
    await expect(bottomPlay).toHaveRole("button");
    await expect(bottomPlay).toHaveAccessibleName("pause");
    await bottomPlay.focus();
    await expect(bottomPlay).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(video).toHaveJSProperty("paused", true);
    if (viewport.name === "mobile") await expect(centerPlay).toBeVisible();
    const pausedTime = await video.evaluate((element: HTMLVideoElement) => element.currentTime);

    const timeline = controller.locator("media-time-range").getByRole("slider");
    await timeline.focus();
    await page.keyboard.press("ArrowRight");
    await expect.poll(() =>
      video.evaluate((element: HTMLVideoElement) => element.currentTime),
    ).toBeGreaterThan(pausedTime + 0.1);
    await expect(video).toHaveJSProperty("paused", true);

    const mute = controller.locator("media-mute-button");
    await mute.click();
    await expect(video).toHaveJSProperty("muted", false);
    const volume = controller.locator("media-volume-range").getByRole("slider");
    await expect(volume).toHaveValue("1");
    await volume.focus();
    await page.keyboard.press("Home");
    await expect(video).toHaveJSProperty("volume", 0);
    await page.keyboard.press("End");
    await expect(video).toHaveJSProperty("volume", 1);
    await mute.click();
    await expect(video).toHaveJSProperty("muted", true);

    const fullscreen = controller.locator("media-fullscreen-button");
    await fullscreen.click();
    await expect.poll(() => page.evaluate(() => document.fullscreenElement !== null)).toBe(true);
    await fullscreen.click();
    await expect.poll(() => page.evaluate(() => document.fullscreenElement !== null)).toBe(false);

    const play = viewport.name === "mobile" ? centerPlay : bottomPlay;
    await play.focus();
    await page.keyboard.press("Enter");
    await expect(video).toHaveJSProperty("paused", false);
    await video.evaluate((element: HTMLVideoElement) => {
      element.pause();
      element.currentTime = 1;
    });
    await expect.poll(() =>
      video.evaluate((element: HTMLVideoElement) => element.seeking),
    ).toBe(false);
    await page.getByRole("button", { name: "Close Learn More" }).focus();
    await controller.hover();
    const bounds = await controller.evaluate(element => element.getBoundingClientRect().toJSON());
    expect(bounds.left).toBeGreaterThanOrEqual(0);
    expect(bounds.right).toBeLessThanOrEqual(viewport.width);
    await controller.screenshot({ path: testInfo.outputPath(`founder-${viewport.name}.png`) });
    await page.getByRole("button", { name: "Close Learn More" }).click();
    await expect(controller).toBeHidden();
    await expect(trigger).toBeFocused();
  });
}
