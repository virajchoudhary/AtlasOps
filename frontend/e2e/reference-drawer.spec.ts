import { expect, test } from "@playwright/test";

const drawerViewports = [
  { name: "desktop-1280", width: 1280, height: 720, layout: "desktop" },
  { name: "desktop-1904", width: 1904, height: 881, layout: "desktop" },
  { name: "tablet-851", width: 851, height: 900, layout: "stacked" },
  { name: "mobile-390", width: 390, height: 844, layout: "stacked" },
  { name: "mobile-375", width: 375, height: 812, layout: "stacked" },
  { name: "mobile-320", width: 320, height: 740, layout: "stacked" },
] as const;

type DrawerRect = {
  x: number;
  y: number;
  width: number;
  height: number;
  right: number;
  bottom: number;
};

for (const viewport of drawerViewports) {
  test(`Learn More drawer framing and index layout at ${viewport.name}`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await page.addInitScript(() => {
      sessionStorage.setItem("content-architecture-newsletter-dismissed", "1");
    });
    const response = await page.goto("/reference");
    expect(response?.status()).toBe(200);

    await page.getByRole("button", { name: /learn more/i }).click();
    const dialog = page.getByRole("dialog", {
      name: "README / The Content Architecture",
    });
    await expect(dialog).toBeVisible();

    await page.screenshot({
      path: testInfo.outputPath(`drawer-${viewport.name}.png`),
    });
    const metrics = await dialog.evaluate((dialogElement) => {
      const index = dialogElement.querySelector<HTMLElement>(".reference-about-index");
      const indexLink = index?.querySelector<HTMLElement>("a");
      const panel = dialogElement.querySelector<HTMLElement>(".reference-about-panel");
      const content = dialogElement.querySelector<HTMLElement>(".reference-about-content");
      const video = dialogElement.querySelector<HTMLElement>(".reference-about-video");
      const title = dialogElement.querySelector<HTMLElement>(".reference-about-header h2");
      const subtitle = dialogElement.querySelector<HTMLElement>(".reference-about-header p");
      const paragraph = dialogElement.querySelector<HTMLElement>(".reference-about-section p");
      const close = dialogElement.querySelector<HTMLElement>(".reference-about-close");
      const sectionHeading = dialogElement.querySelector<HTMLElement>(
        ".reference-about-section h3",
      );

      if (
        !index || !indexLink || !panel || !content || !video || !title
        || !subtitle || !paragraph || !close || !sectionHeading
      ) {
        throw new Error("The Learn More drawer is missing a measured layout element");
      }

      const toRect = (element: Element): DrawerRect => {
        const { x, y, width, height, right, bottom } = element.getBoundingClientRect();
        return { x, y, width, height, right, bottom };
      };
      const dialogStyle = getComputedStyle(dialogElement);
      const panelStyle = getComputedStyle(panel);

      return {
        clientWidth: document.documentElement.clientWidth,
        dialog: toRect(dialogElement),
        index: toRect(index),
        indexLinks: Array.from(index.querySelectorAll("a"), toRect),
        panel: toRect(panel),
        content: toRect(content),
        video: toRect(video),
        title: toRect(title),
        close: toRect(close),
        dialogPadding: [
          dialogStyle.paddingTop,
          dialogStyle.paddingRight,
          dialogStyle.paddingBottom,
          dialogStyle.paddingLeft,
        ],
        panelBackground: panelStyle.backgroundColor,
        panelHorizontalPadding: [panelStyle.paddingLeft, panelStyle.paddingRight],
        indexBackground: getComputedStyle(index).backgroundColor,
        indexPadding: getComputedStyle(index).padding,
        contentClientWidth: content.clientWidth,
        headingFontSize: getComputedStyle(title).fontSize,
        sectionHeadingFontSize: getComputedStyle(sectionHeading).fontSize,
        indexFontSize: getComputedStyle(indexLink).fontSize,
        bodyFontSize: getComputedStyle(paragraph).fontSize,
        subtitleFontSize: getComputedStyle(subtitle).fontSize,
        scrollWidths: {
          dialog: dialogElement.scrollWidth,
          index: index.scrollWidth,
          panel: panel.scrollWidth,
          content: content.scrollWidth,
        },
      };
    });
    await testInfo.attach("drawer-geometry", {
      body: JSON.stringify(metrics, null, 2),
      contentType: "application/json",
    });

    expect(metrics.indexLinks).toHaveLength(3);
    expect(metrics.dialogPadding).toEqual(
      viewport.layout === "desktop"
        ? ["16px", "16px", "16px", "16px"]
        : ["8px", "8px", "8px", "8px"],
    );
    expect(metrics.panelBackground).toBe("rgb(203, 203, 203)");
    expect(metrics.panelHorizontalPadding).toEqual(["16px", "16px"]);
    expect(metrics.indexBackground).toBe("rgb(203, 203, 203)");
    expect(metrics.indexPadding).toBe("16px");
    expect(metrics.headingFontSize).toBe(
      viewport.layout === "desktop" ? "17px" : "14px",
    );
    expect(metrics.sectionHeadingFontSize).toBe(
      viewport.layout === "desktop" ? "15.5px" : "14px",
    );
    expect(metrics.indexFontSize).toBe(
      viewport.layout === "desktop" ? "13.5px" : "12px",
    );
    expect(metrics.bodyFontSize).toBe(
      viewport.layout === "desktop" ? "15.5px" : "14px",
    );
    expect(metrics.subtitleFontSize).toBe(
      viewport.layout === "desktop" ? "15.5px" : "14px",
    );
    expect(metrics.video.width / metrics.video.height).toBeCloseTo(16 / 9, 2);
    expect(Math.abs(metrics.video.width - metrics.contentClientWidth)).toBeLessThanOrEqual(1);
    expect(metrics.scrollWidths.dialog).toBeLessThanOrEqual(metrics.dialog.width + 1);
    expect(metrics.scrollWidths.index).toBeLessThanOrEqual(metrics.index.width + 1);
    expect(metrics.scrollWidths.panel).toBeLessThanOrEqual(metrics.panel.width + 1);
    expect(metrics.scrollWidths.content).toBeLessThanOrEqual(metrics.content.width + 1);
    expect(metrics.close.x).toBeGreaterThanOrEqual(metrics.title.right - 1);

    if (viewport.layout === "desktop") {
      expect(metrics.dialog.width).toBe(960);
      expect(metrics.dialog.x).toBe(metrics.clientWidth - 960);
      expect(metrics.dialog.y).toBe(0);
      expect(metrics.dialog.height).toBe(viewport.height);
      expect(metrics.index.width).toBeGreaterThanOrEqual(175);
      expect(metrics.index.width).toBeLessThanOrEqual(185);
      expect(metrics.panel.x).toBe(metrics.dialog.x + 16 + metrics.index.width);
      expect(metrics.panel.y).toBe(16);
      expect(metrics.panel.height).toBe(viewport.height - 32);
      expect(metrics.panel.right).toBe(metrics.clientWidth - 16);
    } else {
      const expectedIndexHeight = viewport.width <= 600 ? 88 : 90;
      expect(metrics.dialog.x).toBe(0);
      expect(metrics.dialog.width).toBe(metrics.clientWidth);
      expect(metrics.dialog.y).toBe(0);
      expect(metrics.dialog.height).toBe(viewport.height);
      expect(metrics.index.x).toBe(8);
      expect(metrics.index.y).toBe(8);
      expect(metrics.index.width).toBe(metrics.clientWidth - 16);
      expect(metrics.index.height).toBe(expectedIndexHeight);
      expect(metrics.panel.x).toBe(8);
      expect(metrics.panel.y).toBe(8 + expectedIndexHeight);
      expect(metrics.panel.width).toBe(metrics.clientWidth - 16);
      expect(metrics.panel.height).toBe(viewport.height - 16 - expectedIndexHeight);

      for (let index = 0; index < metrics.indexLinks.length; index += 1) {
        const link = metrics.indexLinks[index];
        expect(link.x).toBe(metrics.index.x + 16);
        expect(link.width).toBe(metrics.index.width - 32);
        expect(link.bottom).toBeLessThanOrEqual(metrics.index.bottom);
        if (index > 0) {
          expect(link.y).toBeGreaterThan(metrics.indexLinks[index - 1].y);
          expect(metrics.indexLinks[index - 1].bottom).toBeLessThanOrEqual(link.y);
        }
      }
    }
  });
}

test("About drawer section navigation, reduced motion, and keyboard close lifecycle", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.addInitScript(() => {
    sessionStorage.setItem("content-architecture-newsletter-dismissed", "1");
  });
  await page.goto("/reference");

  const opener = page.getByRole("button", { name: /learn more/i });
  await opener.focus();
  await page.keyboard.press("Enter");

  const dialog = page.getByRole("dialog", {
    name: "README / The Content Architecture",
  });
  const closeButton = dialog.getByRole("button", { name: "Close Learn More" });
  await expect(dialog).toBeVisible();
  await expect(closeButton).toBeFocused();

  const whoAmILink = dialog.getByRole("link", { name: /003\s+Who am I/i });
  await page.keyboard.press("Shift+Tab");
  await expect(whoAmILink).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/#who-am-i$/);

  const sectionVisibility = await dialog
    .locator("#who-am-i h3")
    .evaluate((heading) => {
      const content = heading.closest(".reference-about-content");
      if (!(content instanceof HTMLElement)) {
        throw new Error("The section heading is not inside the drawer's scroll region");
      }
      const contentRect = content.getBoundingClientRect();
      const headingRect = heading.getBoundingClientRect();
      return {
        scrollTop: content.scrollTop,
        contentTop: contentRect.top,
        contentBottom: contentRect.bottom,
        headingTop: headingRect.top,
        headingBottom: headingRect.bottom,
      };
    });
  expect(sectionVisibility.scrollTop).toBeGreaterThan(0);
  expect(sectionVisibility.headingTop).toBeGreaterThanOrEqual(
    sectionVisibility.contentTop - 1,
  );
  expect(sectionVisibility.headingBottom).toBeLessThanOrEqual(
    sectionVisibility.contentBottom + 1,
  );

  await expect
    .poll(() =>
      page.evaluate(() => ({
        reducedMotion: window.matchMedia("(prefers-reduced-motion: reduce)").matches,
        scrollBehavior: getComputedStyle(document.documentElement).scrollBehavior,
      })),
    )
    .toEqual({ reducedMotion: true, scrollBehavior: "auto" });

  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(opener).toBeFocused();

  await page.keyboard.press("Enter");
  await expect(dialog).toBeVisible();
  await expect(closeButton).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(dialog).toBeHidden();
  await expect(opener).toBeFocused();
});
