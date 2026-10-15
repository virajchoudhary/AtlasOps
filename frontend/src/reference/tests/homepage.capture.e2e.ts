import { expect, test } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const frontendRoot = resolve(fileURLToPath(new URL("../../../../", import.meta.url)), "frontend");
const outputDirectory = resolve(
  process.env.REFERENCE_CAPTURE_OUTPUT_DIR ??
    resolve(frontendRoot, "..", "..", "..", ".audit", "completion-20261015", "reference-reconstruction-local"),
);

const viewports = [
  { name: "1920x1080", width: 1920, height: 1080 },
  { name: "1440x900", width: 1440, height: 900 },
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
          display: styles.display,
          position: styles.position,
          margin: styles.margin,
          padding: styles.padding,
          gap: styles.gap,
          gridTemplateColumns: styles.gridTemplateColumns,
        };
      };

      return {
        viewport: { width: innerWidth, height: innerHeight },
        pageHeight: document.documentElement.scrollHeight,
        hero: rect(".reference-hero"),
        heroTrack: rect(".reference-dive-track"),
        heading: rect(".reference-hero h1"),
        navigation: rect(".reference-navigation"),
        mobileTicker: rect(".reference-mobile-ticker"),
        canvas: rect(".reference-hero-artwork canvas"),
        costStory: rect(".reference-cost-story"),
        showcase: {
          intro: rect(".reference-showcase-intro"),
          grid: rect(".reference-showcase-grid"),
          projects: Array.from(document.querySelectorAll(".reference-project"), (project) => {
            const bounds = project.getBoundingClientRect();
            const styles = getComputedStyle(project);
            return {
              name: project.querySelector("h3")?.textContent?.trim() ?? "",
              x: bounds.x,
              y: bounds.y,
              width: bounds.width,
              height: bounds.height,
              marginTop: styles.marginTop,
              gridColumn: styles.gridColumn,
            };
          }),
          testimonial: rect(".reference-testimonial"),
        },
        pricing: {
          heading: rect(".reference-pricing-heading"),
          ticket: rect(".reference-pricing-ticket"),
        },
        faq: {
          heading: rect(".reference-faq-heading"),
          list: rect(".reference-faq-list"),
        },
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

    const scrollStates: Record<
      string,
      { scrollY: number; heroTop: number; heroBottom: number; costStoryTop: number }
    > = {};
    if (viewport.width === 1920) {
      for (const scrollY of [1200, 2500]) {
        await page.evaluate((top) => window.scrollTo(0, top), scrollY);
        scrollStates[String(scrollY)] = await page.evaluate(() => {
          const hero = document.querySelector(".reference-hero");
          const costStory = document.querySelector(".reference-cost-story");
          if (!hero || !costStory) {
            return {
              scrollY: window.scrollY,
              heroTop: Number.NaN,
              heroBottom: Number.NaN,
              costStoryTop: Number.NaN,
            };
          }
          const heroBounds = hero.getBoundingClientRect();
          return {
            scrollY: window.scrollY,
            heroTop: heroBounds.top,
            heroBottom: heroBounds.bottom,
            costStoryTop: costStory.getBoundingClientRect().top,
          };
        });
      }
      expect(scrollStates["1200"].scrollY).toBe(1200);
      expect(scrollStates["1200"].heroTop).toBe(0);
      expect(scrollStates["1200"].costStoryTop).toBe(0);
      expect(scrollStates["2500"].scrollY).toBe(2500);
      expect(Math.abs(scrollStates["2500"].heroTop + 340)).toBeLessThanOrEqual(1);
      expect(Math.abs(scrollStates["2500"].costStoryTop + 340)).toBeLessThanOrEqual(1);
    }

    const sectionStates: Record<string, { scrollY: number; top: number; height: number }> = {};
    for (const id of ["showcase", "pricing", "faq"]) {
      const section = page.locator(`#${id}`);
      await section.scrollIntoViewIfNeeded();
      sectionStates[id] = await section.evaluate((element) => {
        const bounds = element.getBoundingClientRect();
        return { scrollY, top: bounds.top, height: bounds.height };
      });
      await page.screenshot({
        path: resolve(outputDirectory, `homepage-${viewport.name}-${id}.png`),
      });
    }
    await writeFile(
      resolve(outputDirectory, `homepage-${viewport.name}.json`),
      `${JSON.stringify({ geometry, sectionStates, scrollStates, pageErrors, failedResponses }, null, 2)}\n`,
      "utf8",
    );

    expect(geometry.viewport).toEqual({ width: viewport.width, height: viewport.height });
    expect(geometry.faqCount).toBe(17);
    if (viewport.width === 1920) {
      const referenceSectionTops = [
        { id: "features", top: 5012.609375 },
        { id: "the-repo", top: 8413 },
        { id: "showcase", top: 10301 },
      ];
      for (const expected of referenceSectionTops) {
        const section = geometry.sectionPositions.find(({ id }) => id === expected.id);
        expect(Math.abs((section?.top ?? Number.NaN) - expected.top)).toBeLessThanOrEqual(8);
      }
      const referenceSectionHeights = [
        { id: "features", height: 3400.390625 },
        { id: "the-repo", height: 1888 },
      ];
      for (const expected of referenceSectionHeights) {
        const section = geometry.sectionPositions.find(({ id }) => id === expected.id);
        expect(Math.abs((section?.height ?? Number.NaN) - expected.height)).toBeLessThanOrEqual(8);
      }
      expect(geometry.heroTrack?.height).toBe(3240);
      expect(geometry.hero?.position).toBe("sticky");
      expect(geometry.hero?.height).toBe(1080);
      expect(geometry.costStory?.position).toBe("sticky");
      expect(geometry.costStory?.height).toBeCloseTo(2852.609375, 0);
      expect(geometry.showcase.grid).toMatchObject({
        x: 80,
        width: 1760,
        gap: "64px 24px",
        gridTemplateColumns: "868px 868px",
      });
      expect(geometry.showcase.projects.slice(0, 2)).toMatchObject([
        { x: 80, width: 868, marginTop: "0px", gridColumn: "auto" },
        { x: 972, width: 868, marginTop: "0px", gridColumn: "auto" },
      ]);
      expect(geometry.showcase.projects[0].height).toBeCloseTo(520.25, 0);
      expect(geometry.showcase.projects[1].height).toBeCloseTo(520.25, 0);
    }
    expect(pageErrors).toEqual([]);
    expect(failedResponses).toEqual([]);

    await context.close();
  });
}

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
