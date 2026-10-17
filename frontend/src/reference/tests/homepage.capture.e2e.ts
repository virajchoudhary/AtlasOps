import { expect, test, type Page } from "@playwright/test";
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

async function readMotionLayout(page: Page) {
  return page.evaluate(() => {
    const rect = (selector: string) => {
      const element = document.querySelector<HTMLElement>(selector);
      if (!element) return null;
      const bounds = element.getBoundingClientRect();
      const styles = getComputedStyle(element);
      return {
        height: bounds.height,
        position: styles.position,
        marginBottom: Number.parseFloat(styles.marginBottom),
      };
    };

    return {
      motionPreference: window.matchMedia("(prefers-reduced-motion: reduce)").matches
        ? "reduce"
        : "no-preference",
      pageHeight: document.documentElement.scrollHeight,
      runningAnimations: document.getAnimations().filter((animation) => animation.playState === "running").length,
      diveTrack: rect(".reference-dive-track"),
      hero: rect(".reference-hero"),
      costStory: rect(".reference-cost-story"),
      costCopy: rect(".reference-cost-copy"),
      faqHeading: rect(".reference-faq-heading"),
      contentSections: ["time-cost", "features", "the-repo", "showcase", "pricing", "faq"].map((id) => {
        const section = document.getElementById(id);
        return { id, height: section?.getBoundingClientRect().height ?? 0 };
      }),
      canvas: {
        renderer: document.querySelector<HTMLElement>(".reference-hero-artwork .ring-canvas")?.dataset.renderer ?? "missing",
        animationState:
          document.querySelector<HTMLElement>(".reference-hero-artwork .ring-canvas")?.dataset.animationState ?? "missing",
        frame: Number(
          document.querySelector<HTMLElement>(".reference-hero-artwork .ring-canvas")?.dataset.frame ?? "0",
        ),
        width: document.querySelector<HTMLCanvasElement>(".reference-hero-artwork .ring-canvas__webgl")?.width ?? 0,
        height: document.querySelector<HTMLCanvasElement>(".reference-hero-artwork .ring-canvas__webgl")?.height ?? 0,
      },
    };
  });
}

test.beforeAll(async () => {
  await mkdir(outputDirectory, { recursive: true });
});

for (const viewport of viewports) {
  test(`captures the homepage at ${viewport.name}`, async ({ browser }) => {
    const context = await browser.newContext({
      viewport: { width: viewport.width, height: viewport.height },
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
    await expect(page.getByRole("heading", { name: "The stack agents don't reinvent." })).toBeVisible();
    await expect(page.locator(".reference-hero-artwork canvas").first()).toBeVisible();
    const ringCanvas = page.locator(".reference-hero-artwork .ring-canvas");
    await expect(ringCanvas).toHaveAttribute("data-renderer", "webgl2");
    await expect(ringCanvas).toHaveAttribute("data-ring-count", "30");
    await expect(ringCanvas).toHaveAttribute("data-animation-state", "running");
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
          marginBottom: styles.marginBottom,
          padding: styles.padding,
          gap: styles.gap,
          gridTemplateColumns: styles.gridTemplateColumns,
        };
      };

      return {
        viewport: { width: innerWidth, height: innerHeight },
        prefersReducedMotion: matchMedia("(prefers-reduced-motion: reduce)").matches,
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
        await page.waitForFunction((top) => Math.abs(window.scrollY - top) <= 1, scrollY);
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
    expect(geometry.prefersReducedMotion).toBe(false);
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
      expect(geometry.heroTrack?.marginBottom).toBe("-2376px");
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

const pricingTicketViewports = [
  {
    name: "1904x881",
    width: 1904,
    height: 881,
    expectedX: 80,
    expectedWidth: 1744,
    padding: "40px",
    priceFontSize: "280px",
  },
  {
    name: "1024x900",
    width: 1024,
    height: 900,
    expectedX: 56,
    expectedWidth: 912,
    padding: "40px",
    priceFontSize: "180px",
  },
  {
    name: "1200x900",
    width: 1200,
    height: 900,
    expectedX: 80,
    expectedWidth: 1040,
    padding: "40px",
    priceFontSize: null,
  },
  {
    name: "851x900",
    width: 851,
    height: 900,
    expectedX: 47,
    expectedWidth: 757,
    padding: "40px",
    priceFontSize: null,
  },
  {
    name: "320x900",
    width: 320,
    height: 900,
    expectedX: 16,
    expectedWidth: 288,
    padding: "20px",
    priceFontSize: null,
  },
  {
    name: "390x844",
    width: 390,
    height: 844,
    expectedX: 16,
    expectedWidth: 358,
    padding: "20px",
    priceFontSize: "120px",
  },
] as const;

for (const viewport of pricingTicketViewports) {
  test(`renders the pricing ticket at ${viewport.name}`, async ({ browser }) => {
    const context = await browser.newContext({
      viewport: { width: viewport.width, height: viewport.height },
      reducedMotion: "reduce",
    });
    await context.addInitScript(() => {
      try {
        sessionStorage.setItem("content-architecture-newsletter-dismissed", "1");
      } catch {
      }
    });

    const page = await context.newPage();
    await page.goto("/reference.html", { waitUntil: "networkidle" });
    await page.evaluate(() => document.fonts.ready);
    await page.locator("#pricing").scrollIntoViewIfNeeded();

    const ticket = page.locator("#pricing .reference-pricing-ticket");
    await expect(ticket).toBeVisible();
    const geometry = await ticket.evaluate((element) => {
      const section = element.closest<HTMLElement>("#pricing");
      const heading = section?.querySelector<HTMLElement>(".reference-pricing-heading h2");
      const facts = element.querySelector<HTMLElement>(".reference-pricing-facts");
      const details = element.querySelector<HTMLElement>(".reference-pricing-details");
      const benefits = element.querySelector<HTMLElement>(".reference-pricing-benefits");
      const price = element.querySelector<HTMLElement>(".reference-pricing-facts strong");
      const buy = element.querySelector<HTMLElement>(".reference-pricing-buy");
      const adopters = section?.querySelector<HTMLElement>(".reference-early-adopters");
      const avatars = adopters?.querySelector<HTMLElement>(".reference-avatar-stack");
      const adopterLabel = adopters?.querySelector<HTMLElement>("span");
      if (
        !section || !heading || !facts || !details || !benefits || !price || !buy || !avatars || !adopterLabel
      ) {
        throw new Error("Pricing ticket content is incomplete");
      }

      const bounds = (node: Element) => {
        const rect = node.getBoundingClientRect();
        return { x: rect.x, right: rect.right, y: rect.y, bottom: rect.bottom, width: rect.width };
      };
      const rows = Array.from(benefits.querySelectorAll(":scope > li"), (row) => {
        const index = row.querySelector(".reference-benefit-index");
        const text = row.querySelector<HTMLElement>(".reference-benefit-text");
        return {
          index: index?.textContent?.trim() ?? "",
          text: text?.textContent?.trim() ?? "",
          textFits: Boolean(text && text.scrollWidth <= text.clientWidth),
        };
      });
      const adopterBounds = bounds(adopters ?? section);
      const avatarBounds = bounds(avatars);
      const labelBounds = bounds(adopterLabel);
      const ticketStyle = getComputedStyle(element);
      return {
        viewport: { width: window.innerWidth, height: window.innerHeight },
        ticket: {
          ...bounds(element),
          padding: ticketStyle.padding,
          background: ticketStyle.backgroundColor,
          color: ticketStyle.color,
        },
        facts: bounds(facts),
        price: bounds(price),
        details: {
          ...bounds(details),
          borderLeftStyle: getComputedStyle(details).borderLeftStyle,
          borderTopStyle: getComputedStyle(details).borderTopStyle,
        },
        benefits: bounds(benefits),
        buy: bounds(buy),
        priceFontSize: getComputedStyle(price).fontSize,
        priceFits:
          price.getBoundingClientRect().left >= facts.getBoundingClientRect().left &&
          price.getBoundingClientRect().right <=
            facts.getBoundingClientRect().right - Number.parseFloat(getComputedStyle(facts).paddingRight),
        rows,
        headingText: heading.innerText,
        adopters: {
          display: getComputedStyle(adopters ?? section).display,
          ...adopterBounds,
          avatarRight: avatarBounds.right,
          labelX: labelBounds.x,
        },
        formCount: element.querySelectorAll("form").length,
      };
    });

    await page.screenshot({
      path: resolve(outputDirectory, `pricing-ticket-${viewport.name}.png`),
    });
    await writeFile(
      resolve(outputDirectory, `pricing-ticket-${viewport.name}.json`),
      `${JSON.stringify({ viewport, geometry }, null, 2)}\n`,
      "utf8",
    );

    expect.soft(geometry.viewport).toEqual({ width: viewport.width, height: viewport.height });
    expect.soft(Math.abs(geometry.ticket.x - viewport.expectedX)).toBeLessThanOrEqual(1);
    expect.soft(Math.abs(geometry.ticket.width - viewport.expectedWidth)).toBeLessThanOrEqual(1);
    expect.soft(geometry.ticket.right).toBeLessThanOrEqual(viewport.width);
    expect.soft(geometry.ticket.padding).toBe(viewport.padding);
    expect.soft(geometry.ticket.background).toBe("rgb(35, 35, 35)");
    expect.soft(geometry.ticket.color).toBe("rgb(241, 238, 231)");
    if (viewport.priceFontSize !== null) {
      expect.soft(geometry.priceFontSize).toBe(viewport.priceFontSize);
    }
    expect.soft(geometry.priceFits).toBe(true);
    expect.soft(geometry.headingText).toBe("Two editions.\nOne architecture.\nLifetime updates.");
    expect.soft(geometry.adopters.display).toBe("flex");
    expect.soft(geometry.adopters.avatarRight).toBeLessThanOrEqual(geometry.adopters.labelX + 1);
    expect.soft(geometry.rows).toEqual([
      { index: "01", text: "Next.js 16 + Sanity v6 repo", textFits: true },
      { index: "02", text: "Unlimited commercial projects", textFits: true },
      { index: "03", text: "Lifetime updates", textFits: true },
      { index: "04", text: "Agent-ready: skills, MCP, llms.txt", textFits: true },
      { index: "05", text: "Direct support from the maintainer", textFits: true },
    ]);
    expect.soft(geometry.formCount).toBe(0);

    const accessLink = page.locator("#pricing .reference-pricing-buy .reference-access-link");
    await expect(accessLink).toHaveAttribute(
      "href",
      "https://www.contentarchitecture.dev/checkout?plan=next",
    );
    await expect(accessLink).toHaveAttribute("target", "_blank");
    if (viewport.width > 850) {
      expect.soft(geometry.details.borderLeftStyle).toBe("dashed");
      expect.soft(geometry.details.borderTopStyle).not.toBe("dashed");
      const contentWidth = geometry.ticket.width - 2 * Number.parseFloat(viewport.padding) - 2;
      expect.soft(Math.abs(geometry.facts.width / contentWidth - 7 / 12)).toBeLessThan(0.01);
      expect.soft(Math.abs(geometry.details.width / contentWidth - 5 / 12)).toBeLessThan(0.01);
    } else {
      expect.soft(geometry.details.borderTopStyle).toBe("dashed");
      expect.soft(geometry.details.borderLeftStyle).not.toBe("dashed");
      expect.soft(geometry.facts.bottom).toBeLessThanOrEqual(geometry.details.y);
      expect.soft(geometry.buy.y).toBeGreaterThanOrEqual(geometry.benefits.bottom);
    }

    const nextEdition = page.getByRole("radio", { name: "Next.js" });
    const astroEdition = page.getByRole("radio", { name: "Astro" });
    await nextEdition.focus();
    await page.keyboard.press("ArrowRight");
    await expect(astroEdition).toBeChecked();
    await expect(page.locator(".reference-pricing-benefit--edition .reference-benefit-text"))
      .toHaveText("Astro 7 + Sanity v6 repo");
    await expect(accessLink).toHaveAttribute(
      "href",
      "https://www.contentarchitecture.dev/checkout?plan=astro",
    );
    await astroEdition.focus();
    await page.keyboard.press("ArrowRight");
    await expect(nextEdition).toBeChecked();
    await expect(accessLink).toHaveAttribute(
      "href",
      "https://www.contentarchitecture.dev/checkout?plan=next",
    );
    await context.close();
  });
}

const faqControlViewports = [
  { name: "390x844", width: 390, height: 844, minHeight: 48 },
  { name: "1440x900", width: 1440, height: 900, minHeight: 44 },
] as const;

for (const viewport of faqControlViewports) {
  test(`keeps the FAQ access link compact and reachable at ${viewport.name}`, async ({ browser }) => {
    const context = await browser.newContext({
      viewport: { width: viewport.width, height: viewport.height },
      reducedMotion: "reduce",
    });
    await context.route(/^https:\/\/www\.contentarchitecture\.dev\/checkout/, (route) =>
      route.fulfill({
        status: 200,
        contentType: "text/html",
        body: "<!doctype html><title>Checkout intercepted</title>",
      }),
    );
    const page = await context.newPage();
    await page.goto("/reference", { waitUntil: "networkidle" });
    await page.evaluate(() => document.fonts.ready);

    const accessLink = page.locator("#faq .reference-faq-heading > .reference-access-link");
    await expect(accessLink).toBeVisible();
    await accessLink.scrollIntoViewIfNeeded();
    const geometry = await accessLink.evaluate((element) => {
      const faqList = document.querySelector(".reference-faq-list");
      if (!faqList) throw new Error("FAQ list is missing");
      const bounds = element.getBoundingClientRect();
      const listBounds = faqList.getBoundingClientRect();
      return {
        x: bounds.x,
        y: bounds.y,
        right: bounds.right,
        bottom: bounds.bottom,
        width: bounds.width,
        height: bounds.height,
        listX: listBounds.x,
        href: element.getAttribute("href"),
        target: element instanceof HTMLAnchorElement ? element.target : "",
      };
    });
    await writeFile(
      resolve(outputDirectory, `reference-faq-control-${viewport.name}.json`),
      `${JSON.stringify({ viewport, geometry }, null, 2)}\n`,
      "utf8",
    );
    await page.screenshot({
      path: resolve(outputDirectory, `reference-faq-control-${viewport.name}.png`),
    });

    expect(geometry.width).toBeLessThan(180);
    expect(geometry.height).toBeGreaterThanOrEqual(viewport.minHeight);
    expect(geometry.x).toBeGreaterThanOrEqual(0);
    expect(geometry.y).toBeGreaterThanOrEqual(0);
    expect(geometry.right).toBeLessThanOrEqual(viewport.width);
    expect(geometry.bottom).toBeLessThanOrEqual(viewport.height);
    if (viewport.width === 390) {
      expect(Math.abs(geometry.x - geometry.listX)).toBeLessThanOrEqual(1);
    }
    await expect(accessLink).toHaveAttribute(
      "href",
      "https://www.contentarchitecture.dev/checkout?plan=next",
    );
    await expect(accessLink).toHaveAttribute("target", "_blank");

    const checkoutTabPromise = page.waitForEvent("popup");
    await accessLink.click();
    const checkoutTab = await checkoutTabPromise;
    await expect(checkoutTab).toHaveURL("https://www.contentarchitecture.dev/checkout?plan=next");
    await expect(checkoutTab).toHaveTitle("Checkout intercepted");
    await checkoutTab.close();
    await context.close();
  });
}

test("reduced motion removes desktop pinning and preserves the active canvas", async ({ browser }) => {
  const viewport = { width: 1920, height: 1080 };
  const normalContext = await browser.newContext({ viewport, reducedMotion: "no-preference" });
  await normalContext.addInitScript(() => {
    try {
      sessionStorage.setItem("content-architecture-newsletter-dismissed", "1");
    } catch {
      // The footer can still open the newsletter if storage is unavailable.
    }
  });
  const normalPage = await normalContext.newPage();
  await normalPage.goto("/reference.html", { waitUntil: "networkidle" });
  await normalPage.evaluate(() => document.fonts.ready);
  await expect(normalPage.locator(".reference-hero-artwork .ring-canvas"))
    .toHaveAttribute("data-animation-state", "running");
  const normalLayout = await readMotionLayout(normalPage);

  const reducedContext = await browser.newContext({ viewport, reducedMotion: "reduce" });
  await reducedContext.addInitScript(() => {
    try {
      sessionStorage.setItem("content-architecture-newsletter-dismissed", "1");
    } catch {
      // The footer can still open the newsletter if storage is unavailable.
    }
  });
  const reducedPage = await reducedContext.newPage();
  await reducedPage.goto("/reference.html", { waitUntil: "networkidle" });
  await reducedPage.evaluate(() => document.fonts.ready);
  const ringCanvas = reducedPage.locator(".reference-hero-artwork .ring-canvas");
  await expect(ringCanvas).toHaveAttribute("data-renderer", "webgl2");
  await expect(ringCanvas).toHaveAttribute("data-animation-state", "reduced-motion");
  await expect(reducedPage.locator(".reference-hero-artwork .ring-canvas__webgl")).toBeVisible();
  const frameBeforeWait = await ringCanvas.getAttribute("data-frame");
  expect(Number(frameBeforeWait)).toBeGreaterThan(0);
  const reducedLayout = await readMotionLayout(reducedPage);
  await writeFile(
    resolve(outputDirectory, "homepage-motion-layouts-1920x1080.json"),
    `${JSON.stringify({ normalMotion: normalLayout, reducedMotion: reducedLayout }, null, 2)}\n`,
    "utf8",
  );

  expect(normalLayout.motionPreference).toBe("no-preference");
  expect(normalLayout.diveTrack?.height).toBe(3240);
  expect(normalLayout.diveTrack?.marginBottom).toBe(-2376);
  expect(normalLayout.hero?.position).toBe("sticky");
  expect(normalLayout.costStory?.position).toBe("sticky");
  expect(normalLayout.costCopy?.position).toBe("sticky");
  expect(normalLayout.faqHeading?.position).toBe("sticky");
  expect(reducedLayout.motionPreference).toBe("reduce");
  expect(reducedLayout.runningAnimations).toBe(0);
  expect(reducedLayout.diveTrack?.marginBottom).not.toBe(-2376);
  expect(reducedLayout.hero?.position).not.toBe("sticky");
  expect(reducedLayout.costStory?.position).not.toBe("sticky");
  expect(reducedLayout.costCopy?.position).not.toBe("sticky");
  expect(reducedLayout.faqHeading?.position).not.toBe("sticky");
  expect(reducedLayout.pageHeight).toBeLessThan(normalLayout.pageHeight);
  expect(reducedLayout.contentSections.map(({ id }) => id)).toEqual([
    "time-cost",
    "features",
    "the-repo",
    "showcase",
    "pricing",
    "faq",
  ]);
  expect(reducedLayout.contentSections.every(({ height }) => height > 0)).toBe(true);
  expect(reducedLayout.canvas.renderer).toBe("webgl2");
  expect(reducedLayout.canvas.animationState).toBe("reduced-motion");
  expect(reducedLayout.canvas.frame).toBeGreaterThan(0);
  expect(reducedLayout.canvas.width).toBeGreaterThan(0);
  expect(reducedLayout.canvas.height).toBeGreaterThan(0);
  await reducedPage.waitForTimeout(100);
  await expect(ringCanvas).toHaveAttribute("data-frame", frameBeforeWait ?? "");

  const heroAccess = reducedPage.locator(".reference-hero-actions .reference-access-link");
  await expect(heroAccess).toHaveAttribute("href", "#pricing");
  await heroAccess.click();
  await expect(reducedPage).toHaveURL(/#pricing$/);
  await expect(reducedPage.locator("#pricing")).toBeVisible();

  await reducedContext.close();
  await normalContext.close();
});

test("reduced motion preserves mobile content navigation", async ({ browser }) => {
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
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
  await page.goto("/reference.html", { waitUntil: "networkidle" });
  await page.evaluate(() => document.fonts.ready);

  const ringCanvas = page.locator(".reference-hero-artwork .ring-canvas");
  await expect(ringCanvas).toHaveAttribute("data-animation-state", "reduced-motion");
  await expect(page.locator(".reference-hero-artwork .ring-canvas__webgl")).toBeVisible();
  await expect(page.locator(".reference-mobile-ticker")).toBeVisible();
  await page.getByRole("button", { name: /menu/i }).click();
  const mobileNavigation = page.getByRole("navigation", { name: "Page navigation" });
  await expect(mobileNavigation).toBeVisible();
  await mobileNavigation.getByRole("link", { name: "Features" }).click();
  await expect(page).toHaveURL(/#features$/);
  await expect(page.locator("#features")).toBeVisible();

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
  await expect(desktop.locator(".reference-pricing-benefit--edition .reference-benefit-text"))
    .toHaveText("Astro 7 + Sanity v6 repo");
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
