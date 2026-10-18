import { expect, test, type CDPSession, type Locator } from "@playwright/test";

const strictFrontendCsp = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data:",
  "connect-src 'self'",
  "font-src 'self'",
  "object-src 'none'",
  "base-uri 'none'",
  "frame-ancestors 'none'",
].join("; ");

async function dragWithTouch(
  cdp: CDPSession,
  slider: Locator,
  startRatio: number,
  endRatio: number,
) {
  const bounds = await slider.boundingBox();
  if (!bounds || bounds.width < 2 || bounds.height < 1) {
    throw new Error("The media slider does not have a touchable bounding box");
  }

  const pointAt = (ratio: number) => ({
    x: Math.round(bounds.x + ratio * bounds.width),
    y: Math.round(bounds.y + bounds.height / 2),
    id: 1,
  });

  await cdp.send("Input.dispatchTouchEvent", {
    type: "touchStart",
    touchPoints: [pointAt(startRatio)],
  });
  for (let step = 1; step <= 4; step += 1) {
    const ratio = startRatio + ((endRatio - startRatio) * step) / 4;
    await cdp.send("Input.dispatchTouchEvent", {
      type: "touchMove",
      touchPoints: [pointAt(ratio)],
    });
  }
  await cdp.send("Input.dispatchTouchEvent", {
    type: "touchEnd",
    touchPoints: [],
  });
}

test.describe("Founder player touch controls", () => {
  test.use({
    hasTouch: true,
    isMobile: true,
    viewport: { width: 390, height: 844 },
  });

  test("touch playback, seeking, volume, and drawer lifecycle", async ({
    page,
    context,
  }, testInfo) => {
    test.slow();

    const externalRequests: string[] = [];
    const cspErrors: string[] = [];
    const origin = new URL(testInfo.project.use.baseURL ?? "").origin;
    page.on("request", (request) => {
      if (new URL(request.url()).origin !== origin) {
        externalRequests.push(new URL(request.url()).origin);
      }
    });
    page.on("console", (message) => {
      if (message.type() === "error" && /Content Security Policy/u.test(message.text())) {
        cspErrors.push(message.text());
      }
    });

    await page.addInitScript(() => {
      sessionStorage.setItem("content-architecture-newsletter-dismissed", "1");
    });
    const response = await page.goto("/reference");
    expect(response?.status()).toBe(200);
    expect(response?.headers()["content-security-policy"]).toBe(strictFrontendCsp);

    const touchProfile = await page.evaluate(() => ({
      coarsePointer: matchMedia("(pointer: coarse)").matches,
      maxTouchPoints: navigator.maxTouchPoints,
    }));
    expect(touchProfile.coarsePointer).toBe(true);
    expect(touchProfile.maxTouchPoints).toBeGreaterThan(0);

    const opener = page.getByRole("button", { name: /learn more/i });
    const founderAssetResponse = page.waitForResponse((candidate) =>
      new URL(candidate.url()).pathname === "/reference/assets/edoardo-founder-video.mp4",
    );
    await opener.tap();

    const dialog = page.getByRole("dialog", {
      name: "README / The Content Architecture",
    });
    const closeButton = dialog.getByRole("button", { name: "Close Learn More" });
    const controller = dialog.locator(".reference-about-video media-controller");
    const video = dialog.getByLabel("Edoardo Lunardi profile video");
    await expect(dialog).toBeVisible();
    await expect(closeButton).toBeFocused();
    await video.scrollIntoViewIfNeeded();
    await expect(controller).toBeVisible();
    await expect(video).toHaveAttribute(
      "src",
      "/reference/assets/edoardo-founder-video.mp4",
    );
    await expect(video).toHaveJSProperty("controls", false);
    await expect(video).toHaveJSProperty("muted", true);
    await expect(video).toHaveJSProperty("playsInline", true);

    const assetResponse = await founderAssetResponse;
    expect([200, 206]).toContain(assetResponse.status());
    expect(assetResponse.headers()["content-type"]).toContain("video/mp4");
    await expect.poll(() =>
      video.evaluate((element: HTMLVideoElement) => element.readyState),
    ).toBeGreaterThanOrEqual(2);
    await expect.poll(() =>
      video.evaluate((element: HTMLVideoElement) => element.currentTime),
    ).toBeGreaterThan(0.4);
    await expect(video).toHaveJSProperty("paused", false);

    const bottomPlay = controller.locator(".reference-founder-play");
    const centerPlay = controller.locator(".reference-founder-center-play");
    await expect(bottomPlay).toBeVisible();
    await expect(bottomPlay).toHaveRole("button");
    await expect(bottomPlay).toHaveAccessibleName("pause");
    await expect.poll(() =>
      bottomPlay.evaluate((element) => Number(getComputedStyle(element).opacity)),
    ).toBeGreaterThan(0.9);
    await bottomPlay.tap();
    await expect(video).toHaveJSProperty("paused", true);

    await expect(centerPlay).toBeVisible();
    await expect(centerPlay).toHaveRole("button");
    await expect(centerPlay).toHaveAccessibleName("play");
    await centerPlay.tap();
    await expect(video).toHaveJSProperty("paused", false);

    await expect(bottomPlay).toHaveAccessibleName("pause");
    await bottomPlay.tap();
    await expect(video).toHaveJSProperty("paused", true);

    const timeline = controller.locator("media-time-range").getByRole("slider");
    await expect(timeline).toBeVisible();
    const beforeSeek = await video.evaluate((element: HTMLVideoElement) => ({
      currentTime: element.currentTime,
      duration: element.duration,
    }));
    expect(Number.isFinite(beforeSeek.duration)).toBe(true);
    expect(beforeSeek.duration).toBeGreaterThan(1);
    const seekForward = beforeSeek.currentTime < beforeSeek.duration * 0.6;
    const seekTargetRatio = seekForward ? 0.8 : 0.2;
    const cdp = await context.newCDPSession(page);
    await dragWithTouch(cdp, timeline, seekForward ? 0.15 : 0.85, seekTargetRatio);
    if (seekForward) {
      await expect.poll(() =>
        video.evaluate((element: HTMLVideoElement) => element.currentTime),
      ).toBeGreaterThan(beforeSeek.currentTime + 0.5);
    } else {
      await expect.poll(() =>
        video.evaluate((element: HTMLVideoElement) => element.currentTime),
      ).toBeLessThan(beforeSeek.currentTime - 0.5);
    }
    await expect(video).toHaveJSProperty("paused", true);
    await expect.poll(() =>
      video.evaluate((element: HTMLVideoElement) => element.seeking),
    ).toBe(false);
    const afterSeek = await video.evaluate((element: HTMLVideoElement) => ({
      currentTime: element.currentTime,
      paused: element.paused,
      seeking: element.seeking,
    }));
    expect(afterSeek.paused).toBe(true);
    expect(afterSeek.seeking).toBe(false);
    await testInfo.attach("founder-touch-seek.json", {
      body: JSON.stringify({
        beforeSeconds: beforeSeek.currentTime,
        afterSeconds: afterSeek.currentTime,
        deltaSeconds: afterSeek.currentTime - beforeSeek.currentTime,
        durationSeconds: beforeSeek.duration,
        pausedAfterSeek: afterSeek.paused,
      }, null, 2),
      contentType: "application/json",
    });

    const mute = controller.locator("media-mute-button");
    await expect(mute).toBeVisible();
    await mute.tap();
    await expect(video).toHaveJSProperty("muted", false);

    const volume = controller.locator("media-volume-range").getByRole("slider");
    await expect(volume).toBeVisible();
    const beforeVolume = await video.evaluate((element: HTMLVideoElement) => element.volume);
    await dragWithTouch(cdp, volume, 0.85, 0.3);
    await expect.poll(() =>
      video.evaluate((element: HTMLVideoElement) => element.volume),
    ).toBeLessThan(beforeVolume - 0.15);
    const afterVolume = await video.evaluate((element: HTMLVideoElement) => element.volume);
    expect(afterVolume).toBeGreaterThan(0.15);
    expect(afterVolume).toBeLessThan(0.6);
    await mute.tap();
    await expect(video).toHaveJSProperty("muted", true);
    await cdp.detach();

    const playerBounds = await controller.boundingBox();
    expect(playerBounds).not.toBeNull();
    if (!playerBounds) throw new Error("The founder player is missing its layout bounds");
    expect(playerBounds.x).toBeGreaterThanOrEqual(0);
    expect(playerBounds.x + playerBounds.width).toBeLessThanOrEqual(391);
    const pageBounds = await page.evaluate(() => ({
      clientWidth: document.documentElement.clientWidth,
      scrollWidth: document.documentElement.scrollWidth,
    }));
    expect(pageBounds.scrollWidth).toBeLessThanOrEqual(pageBounds.clientWidth);
    const dialogBounds = await dialog.evaluate((element) => ({
      clientWidth: element.clientWidth,
      scrollWidth: element.scrollWidth,
    }));
    expect(dialogBounds.scrollWidth).toBeLessThanOrEqual(dialogBounds.clientWidth + 1);
    await controller.screenshot({ path: testInfo.outputPath("founder-touch-player.png") });
    await page.screenshot({ path: testInfo.outputPath("founder-touch-page.png") });

    await closeButton.tap();
    await expect(dialog).toBeHidden();
    await expect(opener).toBeFocused();
    await opener.tap();
    await expect(dialog).toBeVisible();
    await expect(closeButton).toBeFocused();
    await closeButton.tap();
    await expect(dialog).toBeHidden();
    await expect(opener).toBeFocused();

    expect(externalRequests).toEqual([]);
    expect(cspErrors).toEqual([]);
  });
});
