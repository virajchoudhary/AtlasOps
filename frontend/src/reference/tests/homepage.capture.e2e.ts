import { expect, test } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const frontendRoot = resolve(fileURLToPath(new URL("../../../../", import.meta.url)), "frontend");
const outputDirectory = resolve(frontendRoot, "..", "..", "..", ".audit", "completion-20261015", "reference-geometry-final-20261015");

const viewports = [
  { name: "1920x1080", width: 1920, height: 1080 },
  { name: "1440x900", width: 1440, height: 900 },
  { name: "1904x881-live-measurement", width: 1904, height: 881 },
  { name: "1280x800", width: 1280, height: 800 },
  { name: "768x1024", width: 768, height: 1024 },
  { name: "390x844", width: 390, height: 844 },
] as const;

test.beforeAll(async () => {
  await mkdir(outputDirectory, { recursive: true });
});

for (const viewport of viewports) {
  test(`captures the homepage at ${viewport.name}`, async ({ browser }) => {
    const context = await browser.newContext({
      viewport: { width: viewport.width, height: viewport.height },
      reducedMotion: "reduce",
    });
    await context.addInitScript(() => {
      try {
        sessionStorage.setItem("content-architecture-newsletter-dismissed", "1");
      } catch {
        // The footer can still open the newsletter if storage is unavailable.
      }
    });

    const page = await context.newPage();
    const pageErrors: string[] = [];
    const failedResponses: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    page.on("response", (response) => {
      if (response.status() >= 400) failedResponses.push(`${response.status()} ${response.url()}`);
    });

    await page.goto("/reference.html", { waitUntil: "networkidle" });
    await page.evaluate(() => document.fonts.ready);
    await expect(page.getByRole("heading", { name: "The stack agents don't reinvent." })).toBeVisible();
    await expect(page.locator(".reference-hero-artwork canvas").first()).toBeVisible();
    const ringCanvas = page.locator(".reference-hero-artwork .ring-canvas");
    await expect(ringCanvas).toHaveAttribute("data-renderer", "webgl2");
    await expect(ringCanvas).toHaveAttribute("data-ring-count", "30");
    await expect(ringCanvas).toHaveAttribute("data-animation-state", "reduced-motion");
    const expectedHeadingBreak =
      viewport.width >= 1200 && viewport.width <= 1535 ? "block" : "none";
    await expect(page.locator(".reference-hero-heading-break")).toHaveCSS("display", expectedHeadingBreak);
    if (viewport.width === 1440) {
      await expect(page.locator(".reference-desktop-nav a").first()).toHaveCSS("font-size", "12px");
      await expect(page.locator(".reference-brand-desktop img")).toBeVisible();
    }

    const geometry = await page.evaluate(() => {
      const rect = (selector: string) => {
        const element = document.querySelector(selector);
        if (!element) return null;
        const bounds = element.getBoundingClientRect();
        const styles = getComputedStyle(element);
        return {
          x: bounds.x,
          y: bounds.y,
          width: bounds.width,
          height: bounds.height,
          font: styles.font,
        };
      };

      return {
        viewport: { width: innerWidth, height: innerHeight },
        pageHeight: document.documentElement.scrollHeight,
        hero: rect(".reference-hero"),
        heading: rect(".reference-hero h1"),
        navigation: rect(".reference-navigation"),
        mobileTicker: rect(".reference-mobile-ticker"),
        canvas: rect(".reference-hero-artwork canvas"),
        sectionPositions: ["time-cost", "features", "the-repo", "showcase", "pricing", "faq"].map((id) => {
          const section = document.getElementById(id);
          if (!section) return { id, top: null, height: null };
          const bounds = section.getBoundingClientRect();
          return { id, top: bounds.top + scrollY, height: bounds.height };
        }),
        faqCount: document.querySelectorAll(".reference-faq-item").length,
      };
    });

    await page.screenshot({
      path: resolve(outputDirectory, `homepage-${viewport.name}.png`),
    });
    await writeFile(
      resolve(outputDirectory, `homepage-${viewport.name}.json`),
      `${JSON.stringify({ geometry, pageErrors, failedResponses }, null, 2)}\n`,
      "utf8",
    );

    expect(geometry.viewport).toEqual({ width: viewport.width, height: viewport.height });
    expect(geometry.faqCount).toBe(17);
    expect(pageErrors).toEqual([]);
    expect(failedResponses).toEqual([]);

    await context.close();
  });
}

test("matches the live desktop cost and repository scroll geometry", async ({ browser }) => {
  const context = await browser.newContext({
    viewport: { width: 1904, height: 881 },
    reducedMotion: "no-preference",
  });
  await context.addInitScript(() => {
    try {
      sessionStorage.setItem("content-architecture-newsletter-dismissed", "1");
    } catch {
      // The footer can still open the newsletter if storage is unavailable.
    }
  });

  const page = await context.newPage();
  const pageErrors: string[] = [];
  const failedResponses: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  page.on("response", (response) => {
    if (response.status() >= 400) failedResponses.push(`${response.status()} ${response.url()}`);
  });

  await page.goto("/reference.html", { waitUntil: "networkidle" });
  await page.evaluate(() => document.fonts.ready);

  await page.evaluate(() => {
    document.documentElement.style.scrollBehavior = "auto";
  });

  const geometry = await page.evaluate(() => {
    const section = (id: string) => {
      const element = document.getElementById(id);
      if (!element) return null;
      const bounds = element.getBoundingClientRect();
      return { top: bounds.top + scrollY, height: bounds.height };
    };

    return {
      viewport: { width: innerWidth, height: innerHeight },
      heroTrack: (() => {
        const element = document.querySelector(".reference-hero-track");
        if (!element) return null;
        const bounds = element.getBoundingClientRect();
        return { top: bounds.top + scrollY, height: bounds.height };
      })(),
      features: section("features"),
      repository: section("the-repo"),
      showcase: section("showcase"),
    };
  });

  const within = (
    actual: number | undefined,
    expected: number | undefined,
    tolerance: number,
  ) => actual !== undefined && expected !== undefined && Math.abs(actual - expected) <= tolerance;

  await page.screenshot({ path: resolve(outputDirectory, "homepage-1904x881-no-preference.png") });
  await page.locator(".reference-scroll-cue").click();
  const costScrollY = await page.evaluate(() => scrollY);
  const expectedCostScrollY = geometry.heroTrack
    ? geometry.heroTrack.height - geometry.viewport.height
    : undefined;
  expect(within(costScrollY, expectedCostScrollY, 2)).toBe(true);
  await expect(page.locator(".reference-hero")).toHaveCSS("visibility", "hidden", {
    timeout: 1_000,
  });
  const costHeading = page.locator(".reference-cost-copy .reference-reveal");
  await expect(costHeading).toHaveClass(/is-visible/, { timeout: 1_000 });
  await expect(costHeading).toHaveCSS("opacity", "1", { timeout: 2_000 });
  const costAnchor = await page.evaluate(() => {
    const selectors = [
      ".reference-hero-track",
      ".reference-hero",
      "#time-cost",
      ".reference-cost-stage",
      ".reference-cost-copy",
      ".reference-cost-copy .reference-reveal",
      ".reference-cost-table",
      "#features",
    ];
    return Object.fromEntries(
      selectors.map((selector) => {
        const element = document.querySelector(selector);
        if (!element) return [selector, null];
        const bounds = element.getBoundingClientRect();
        const styles = getComputedStyle(element);
        return [
          selector,
          {
            top: Math.round(bounds.top * 100) / 100,
            height: Math.round(bounds.height * 100) / 100,
            position: styles.position,
            visibility: styles.visibility,
            opacity: styles.opacity,
            zIndex: styles.zIndex,
            className: element.className,
          },
        ];
      }),
    );
  });
  await writeFile(
    resolve(outputDirectory, "cost-anchor-diagnostic.json"),
    `${JSON.stringify({ costScrollY, costAnchor }, null, 2)}\n`,
    "utf8",
  );
  const costCopyTop = costAnchor[".reference-cost-copy"]?.top;
  const costHeadingTop = costAnchor[".reference-cost-copy .reference-reveal"]?.top;
  const costTableTop = costAnchor[".reference-cost-table"]?.top;
  await page.screenshot({
    path: resolve(outputDirectory, "section-cost-1904x881-no-preference.png"),
  });

  for (const [id, name] of [
    ["the-repo", "repo"],
    ["showcase", "showcase"],
  ] as const) {
    await page.evaluate((sectionId) => {
      const element = document.getElementById(sectionId);
      if (!element) return;
      window.scrollTo(0, element.getBoundingClientRect().top + window.scrollY);
    }, id);
    await page.evaluate(
      () =>
        new Promise<void>((resolveFrame) => {
          requestAnimationFrame(() => requestAnimationFrame(() => resolveFrame()));
        }),
    );
    await page.screenshot({
      path: resolve(outputDirectory, `section-${name}-1904x881-no-preference.png`),
    });
  }
  await writeFile(
    resolve(outputDirectory, "desktop-no-preference-geometry.json"),
    `${JSON.stringify({ geometry, pageErrors, failedResponses }, null, 2)}\n`,
    "utf8",
  );

  expect(geometry.viewport).toEqual({ width: 1904, height: 881 });
  expect(within(geometry.features?.top, 4416, 20)).toBe(true);
  expect(within(geometry.repository?.top, 7816, 20)).toBe(true);
  expect(within(geometry.repository?.height, 1570, 20)).toBe(true);
  expect(within(geometry.showcase?.top, 9386, 25)).toBe(true);
  expect(within(costCopyTop, 106, 3)).toBe(true);
  expect(within(costHeadingTop, 167, 3)).toBe(true);
  expect(within(costTableTop, 106, 3)).toBe(true);
  expect(pageErrors).toEqual([]);
  expect(failedResponses).toEqual([]);

  await context.close();
});

test("keeps reference controls local and interactive", async ({ browser, baseURL }) => {
  const errors: string[] = [];
  const desktopContext = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    reducedMotion: "reduce",
  });
  await desktopContext.addInitScript(() => {
    try {
      sessionStorage.setItem("content-architecture-newsletter-dismissed", "1");
    } catch {
      // The footer can still open the newsletter if storage is unavailable.
    }
  });
  const desktop = await desktopContext.newPage();
  desktop.on("pageerror", (error) => errors.push(error.message));
  await desktop.goto("/reference.html", { waitUntil: "networkidle" });

  const heroAccess = desktop.locator(".reference-hero-actions .reference-access-link");
  await expect(heroAccess).toHaveAttribute("href", "#pricing");
  await heroAccess.click();
  await expect(desktop).toHaveURL(/#pricing$/);

  await desktop.getByRole("button", { name: "Next testimonial" }).click();
  await expect(desktop.locator(".reference-testimonial-controls")).toContainText("02 / 03");

  const edition = desktop.locator('input[name="reference-edition"][value="astro"]');
  await edition.check();
  await expect(desktop.locator(".reference-pricing-facts h3")).toContainText("Astro 7");
  await expect(desktop.locator(".reference-faq-edition select")).toHaveValue("astro");

  await desktop.getByRole("button", { name: /what is this not/i }).click();
  await expect(desktop.locator(".reference-faq-trigger[aria-expanded=true]")).toHaveCount(1);

  await desktop.keyboard.press("Control+j");
  await expect(desktop.getByRole("button", { name: "Show terminal" })).toBeVisible();
  await desktop.keyboard.press("Control+j");
  await expect(desktop.getByRole("button", { name: "Hide terminal" })).toBeVisible();
  await desktop.keyboard.press("Control+k");
  await expect(desktop.getByRole("dialog", { name: "Search files" })).toBeVisible();
  await desktop.getByRole("textbox", { name: "Search files" }).fill("README");
  const readmeResults = desktop.locator(".reference-search-dialog li");
  await expect(readmeResults).toHaveCount(2);
  await expect(readmeResults.first()).toContainText("README.md");
  await expect(readmeResults.last()).toContainText("README.md");
  await desktop.keyboard.press("Escape");
  await expect(desktop.getByRole("dialog", { name: "Search files" })).toHaveCount(0);

  await desktop.getByRole("button", { name: /learn more/i }).click();
  await expect(desktop.getByRole("dialog", { name: /readme \/ the content architecture/i })).toBeVisible();
  await desktop.keyboard.press("Escape");
  await expect(desktop.locator(".reference-about-dialog")).toHaveCount(0);

  await desktop.getByRole("button", { name: /watch reel/i }).click();
  await expect(desktop.getByRole("dialog", { name: "Watch reel" })).toBeVisible();
  await desktop.keyboard.press("Escape");
  await expect(desktop.locator(".reference-reel-dialog")).toHaveCount(0);

  const mobileContext = await browser.newContext({
    viewport: { width: 390, height: 844 },
    reducedMotion: "reduce",
  });
  await mobileContext.addInitScript(() => {
    try {
      sessionStorage.setItem("content-architecture-newsletter-dismissed", "1");
    } catch {
      // The footer can still open the newsletter if storage is unavailable.
    }
  });
  const mobile = await mobileContext.newPage();
  mobile.on("pageerror", (error) => errors.push(error.message));
  await mobile.goto("/reference.html", { waitUntil: "networkidle" });
  await expect(mobile.locator(".reference-mobile-ticker")).toBeVisible();
  await mobile.getByRole("button", { name: /menu/i }).click();
  const mobileNavigation = mobile.getByRole("navigation", { name: "Page navigation" });
  await expect(mobileNavigation).toBeVisible();
  await mobileNavigation.getByRole("link", { name: "Features" }).click();
  await expect(mobile).toHaveURL(/#features$/);
  await expect(mobileNavigation).toHaveCount(0);

  const newsletterPage = await mobileContext.newPage();
  newsletterPage.on("pageerror", (error) => errors.push(error.message));
  await newsletterPage.addInitScript(() => {
    try {
      sessionStorage.removeItem("content-architecture-newsletter-dismissed");
    } catch {
      // The timed dialog can still be checked in this fresh page session.
    }
  });
  await newsletterPage.goto("/reference.html", { waitUntil: "networkidle" });
  await expect(newsletterPage.getByRole("dialog", { name: /not buying today\? stay close/i })).toBeVisible({
    timeout: 15_000,
  });
  await newsletterPage.screenshot({
    path: resolve(outputDirectory, "homepage-390x844-newsletter.png"),
  });
  await newsletterPage.locator(".reference-newsletter-dialog input[type=email]").fill("local@example.test");
  await newsletterPage.locator(".reference-newsletter-dialog button[type=submit]").click();
  await expect(newsletterPage.locator(".reference-newsletter-dialog [role=status]"))
    .toContainText("does not submit email addresses");
  await newsletterPage.getByRole("button", { name: "Close newsletter dialog" }).click();
  await expect(newsletterPage.locator(".reference-newsletter-dialog")).toHaveCount(0);

  const checkoutHrefs = await desktop.locator(".reference-access-link").evaluateAll((links) =>
    links.map((link) => (link instanceof HTMLAnchorElement ? link.href : "")),
  );
  expect(checkoutHrefs.every((href) => href.includes("#pricing") || href.includes("/checkout?plan="))).toBe(true);
  expect(errors).toEqual([]);

  await newsletterPage.close();
  await mobileContext.close();
  await desktopContext.close();
});
