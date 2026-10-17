import { expect, test } from "@playwright/test";

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

const mediaViewports = [
  { name: "desktop", width: 1440, height: 900 },
  { name: "mobile", width: 390, height: 844 },
] as const;

for (const viewport of mediaViewports) {
  test(`reference videos play from local assets under the strict CSP on ${viewport.name}`, async ({
    page,
  }, testInfo) => {
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

    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await page.addInitScript(() => {
      sessionStorage.setItem("content-architecture-newsletter-dismissed", "1");
    });
    const response = await page.goto("/reference");
    expect(response?.status()).toBe(200);
    expect(response?.headers()["content-security-policy"]).toBe(strictFrontendCsp);

    const watchButton = page.getByRole("button", { name: /watch reel/i });
    const reelResponse = page.waitForResponse((candidate) =>
      new URL(candidate.url()).pathname.endsWith("/content-architecture-reel.mp4"),
    );
    await watchButton.click();

    const reelDialog = page.getByRole("dialog", { name: "Watch reel" });
    const reel = page.getByLabel("The Content Architecture product reel");
    await expect(reelDialog).toBeVisible();
    await expect(reel).toHaveAttribute(
      "src",
      "/reference/assets/content-architecture-reel.mp4",
    );
    await expect(reel).toHaveJSProperty("autoplay", true);
    await expect(reel).toHaveJSProperty("controls", true);
    await expect(reel).toHaveJSProperty("playsInline", true);
    await expect(page.getByRole("button", { name: "Close" })).toBeFocused();

    const responseForReel = await reelResponse;
    expect([200, 206]).toContain(responseForReel.status());
    expect(responseForReel.headers()["content-type"]).toContain("video/mp4");
    await expect.poll(() =>
      reel.evaluate((element: HTMLVideoElement) => element.readyState),
    ).toBeGreaterThanOrEqual(2);
    await expect.poll(() =>
      reel.evaluate((element: HTMLVideoElement) => element.videoWidth),
    ).toBeGreaterThan(0);
    await expect.poll(() =>
      reel.evaluate((element: HTMLVideoElement) => element.currentTime),
    ).toBeGreaterThan(0.4);
    await expect(reel).not.toHaveJSProperty("paused", true);
    await page.screenshot({
      path: testInfo.outputPath(`reference-reel-${viewport.name}.png`),
    });

    await page.keyboard.press("Escape");
    await expect(reelDialog).toBeHidden();
    await expect(watchButton).toBeFocused();

    const learnMoreButton = page.getByRole("button", { name: /learn more/i });
    await learnMoreButton.click();
    const aboutDialog = page.getByRole("dialog", {
      name: "README / The Content Architecture",
    });
    const founderVideo = page.getByLabel("Edoardo Lunardi profile video");
    await founderVideo.scrollIntoViewIfNeeded();
    await expect(founderVideo).toHaveAttribute(
      "src",
      "/reference/assets/edoardo-founder-video.mp4",
    );
    await expect(founderVideo).toHaveJSProperty("autoplay", true);
    await expect(founderVideo).toHaveJSProperty("controls", true);
    await expect(founderVideo).toHaveJSProperty("muted", true);
    await expect(founderVideo).toHaveJSProperty("loop", true);
    await expect(founderVideo).toHaveJSProperty("playsInline", true);
    await expect.poll(() =>
      founderVideo.evaluate((element: HTMLVideoElement) => element.readyState),
    ).toBeGreaterThanOrEqual(2);
    await expect.poll(() =>
      founderVideo.evaluate((element: HTMLVideoElement) => element.videoWidth),
    ).toBeGreaterThan(0);
    await expect.poll(() =>
      founderVideo.evaluate((element: HTMLVideoElement) => element.currentTime),
    ).toBeGreaterThan(0.4);

    await page.screenshot({
      path: testInfo.outputPath(`reference-founder-video-${viewport.name}.png`),
    });
    await page.getByRole("button", { name: "Close Learn More" }).click();
    await expect(aboutDialog).toBeHidden();
    await expect(learnMoreButton).toBeFocused();

    expect(externalRequests).toEqual([]);
    expect(cspErrors).toEqual([]);
  });
}
