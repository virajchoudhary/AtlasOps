import { execFileSync } from "node:child_process";
import { writeFile } from "node:fs/promises";
import { expect, test } from "@playwright/test";

const routes = [
  ["Overview", "/", "AtlasOps"],
  ["Rehearsal", "/demo", "Incident workflow"],
  ["Agents", "/agents", "Four roles. Clear authority boundaries."],
  ["Models", "/models", "Model lineage"],
  ["Evaluations", "/evaluations", "Evaluations"],
  ["Incidents", "/incidents", "Incident chronology"],
  ["Evidence", "/evidence", "Provenance before presentation."],
  ["Runbooks", "/runbooks", "Runbook catalog"],
  ["System", "/system", "A local, read-only presentation surface."],
] as const;

async function inspectLoadedFonts(page: import("@playwright/test").Page) {
  return page.evaluate(async () => {
    await document.fonts.ready;
    const normalizeFamily = (family: string) =>
      family.replace(/^["']|["']$/g, "").trim().toLowerCase();
    const fontFaces = [...document.fonts]
      .map((fontFace) => ({
        family: normalizeFamily(fontFace.family),
        status: fontFace.status,
      }))
      .filter(({ family }) => family === "geist" || family === "geist mono");
    return {
      geist: fontFaces.some((fontFace) => fontFace.family === "geist" && fontFace.status === "loaded"),
      geistMono: fontFaces.some((fontFace) => fontFace.family === "geist mono" && fontFace.status === "loaded"),
      fontFaces,
    };
  });
}
const referenceRoutes = [
  { id: "home", path: "/reference", heading: /The stack agents/ },
  { id: "blog", path: "/blog", heading: "Knowledge Hub" },
  { id: "articles", path: "/blog/articles", heading: "Knowledge Hub" },
  {
    id: "projects",
    path: "/blog/the-content-architecture-four-months-in",
    heading: "What Four Months of Real Projects Changed in The Content Architecture",
  },
  {
    id: "agents",
    path: "/blog/serve-content-to-ai-agents-llms-txt-markdown-sanity",
    heading: "Your Site Is Invisible to AI Agents (and Your CMS Can Fix It)",
  },
  {
    id: "caching",
    path: "/blog/stale-sanity-content-nextjs-caching-revalidation",
    heading: "Stale Sanity Content in Next.js and Astro: The Two Caches and How to Fix Them",
  },
  {
    id: "projects-alias",
    path: "/blog/articles/the-content-architecture-four-months-in",
    heading: "What Four Months of Real Projects Changed in The Content Architecture",
  },
  {
    id: "agents-alias",
    path: "/blog/articles/serve-content-to-ai-agents-llms-txt-markdown-sanity",
    heading: "Your Site Is Invisible to AI Agents (and Your CMS Can Fix It)",
  },
  {
    id: "caching-alias",
    path: "/blog/articles/stale-sanity-content-nextjs-caching-revalidation",
    heading: "Stale Sanity Content in Next.js and Astro: The Two Caches and How to Fix Them",
  },
  { id: "roadmap", path: "/roadmap", heading: "Roadmap" },
  { id: "privacy", path: "/legal/privacy-policy", heading: "Privacy Policy" },
  { id: "terms", path: "/legal/terms-of-service", heading: "Terms of Service" },
] as const;

test("overview shows API inventory and stays distinct from certification", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1, name: "AtlasOps" })).toBeVisible();
  await expect(page.getByText("Governed Multi-Agent SRE Intelligence")).toHaveCount(0);
  await expect(page.getByText("This is the actual repo.", { exact: true })).toHaveCount(0);
  await expect(page.getByText("Read-only research demo")).toBeVisible();
  await expect(page.getByText(/GAI\s*\+\s*RL/i)).toHaveCount(0);

  const tools = page.locator(".overview-inventory-row").filter({ hasText: "Registered tool wrappers" });
  await expect(tools.locator("dd")).toHaveText("24");
  const roleExposed = page.locator(".overview-inventory-row").filter({ hasText: "Role-exposed tools" });
  await expect(roleExposed.locator("dd")).toHaveText("19");

  const scenarios = page.locator(".overview-inventory-row").filter({ hasText: "Frozen scenarios" });
  await expect(scenarios.locator("dd")).toHaveText("28");
  await expect(page.locator(".overview-posture")).toContainText("NOT_CERTIFIED");
  await expect(page.locator(".overview-posture")).toContainText("READY_FOR_REVIEW");
  await expect(page.locator(".overview-evidence-ledger")).toContainText("No diagnostic improvement observed");
  await expect(page.locator(".overview-evidence-ledger")).toContainText("0 / 8");
  await expect(page.locator(".overview-evidence-ledger")).toContainText("Accepted SFT+GRPO checkpoint");
});

test("every primary route is reachable through the hash router", async ({ page }) => {
  for (const [label, path, heading] of routes) {
    await page.goto(`/#${path}`);
    await expect(page.getByRole("heading", { level: 1, name: heading })).toBeVisible();
    await expect(page.getByText(/GAI\s*\+\s*RL/i)).toHaveCount(0);
  }
});

test("refresh and catalog search remain available in the redesigned shell", async ({ page }) => {
  const methods: string[] = [];
  page.on("request", (request) => {
    if (new URL(request.url()).pathname.startsWith("/api/")) methods.push(request.method());
  });
  await page.goto("/");
  const apiStatus = page.locator(".api-indicator");
  await expect(apiStatus).toContainText("Snapshot available");
  await page.getByRole("button", { name: "Refresh repository snapshot" }).click();
  await expect(apiStatus).toContainText("Snapshot available");

  await page.goto("/#/runbooks");
  const search = page.getByPlaceholder("Search catalog");
  await expect(page.locator(".runbook-row")).not.toHaveCount(0);
  await search.fill("zz-no-matching-runbook-zz");
  await expect(page.locator(".runbook-row")).toHaveCount(0);
  await expect(page.getByText("No runbooks match this search.")).toBeVisible();
  await search.fill("");
  await expect(page.locator(".runbook-row").first()).toBeVisible();

  expect(methods.length).toBeGreaterThan(0);
  expect(methods.every((method) => method === "GET")).toBe(true);
});

test("nine routes render at the five reference viewports without overlap or overflow", async ({ page }, testInfo) => {
  const viewports = [
    { name: "1920x1080", width: 1920, height: 1080 },
    { name: "1440x900", width: 1440, height: 900 },
    { name: "1280x800", width: 1280, height: 800 },
    { name: "768x1024", width: 768, height: 1024 },
    { name: "390x844", width: 390, height: 844 },
  ] as const;
  const methods: string[] = [];
  const apiRequests: string[] = [];
  const runtimeErrors: string[] = [];
  const consoleErrors: string[] = [];
  const records: {
    route: string;
    viewport: string;
    apiMethods: string[];
    apiRequests: string[];
    fontsLoaded: {
      geist: boolean;
      geistMono: boolean;
      fontFaces: { family: string; status: string }[];
    };
    documentWidth: number;
    viewportWidth: number;
    overlapCount: number | null;
  }[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname.startsWith("/api/")) {
      methods.push(request.method());
      apiRequests.push(`${request.method()} ${url.pathname}`);
    }
  });
  const getErrors: string[] = [];
  page.on("response", (response) => {
    const request = response.request();
    if (request.method() === "GET" && response.status() >= 400) {
      getErrors.push(`GET ${new URL(response.url()).pathname} ${response.status()}`);
    }
  });
  page.on("requestfailed", (request) => {
    if (request.method() === "GET") {
      getErrors.push(`GET ${new URL(request.url()).pathname} ${request.failure()?.errorText ?? "request failed"}`);
    }
  });
  page.on("pageerror", (error) => runtimeErrors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });

  for (const viewport of viewports) {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    for (const [, path, heading] of routes) {
      const requestOffset = methods.length;
      const apiRequestOffset = apiRequests.length;
      await page.goto(`/#${path}`);
      await expect(page.getByRole("heading", { level: 1, name: heading })).toBeVisible();
      const fontsLoaded = await inspectLoadedFonts(page);
      expect(fontsLoaded.geist, `${path} Geist Sans`).toBe(true);
      expect(fontsLoaded.geistMono, `${path} Geist Mono`).toBe(true);
      const layout = await page.evaluate(() => {
        const children = [...document.querySelectorAll<HTMLElement>(".page-stack > *")]
          .filter((element) => {
            const bounds = element.getBoundingClientRect();
            return bounds.width > 0 && bounds.height > 0;
          })
          .map((element) => element.getBoundingClientRect());
        let overlapCount = 0;
        for (let left = 0; left < children.length; left++) {
          for (let right = left + 1; right < children.length; right++) {
            const first = children[left];
            const second = children[right];
            if (
              first.left < second.right
              && first.right > second.left
              && first.top < second.bottom - 1
              && first.bottom > second.top + 1
            ) overlapCount += 1;
          }
        }
        return {
          documentWidth: document.documentElement.scrollWidth,
          viewportWidth: window.innerWidth,
          overlapCount,
        };
      });
      const apiMethods = methods.slice(requestOffset);
      const routeApiRequests = apiRequests.slice(apiRequestOffset);
      expect(layout.documentWidth, `${path} at ${viewport.name}`).toBeLessThanOrEqual(layout.viewportWidth);
      expect(layout.overlapCount, `${path} at ${viewport.name}`).toBe(0);
      expect(apiMethods.every((method) => method === "GET"), `${path} API methods`).toBe(true);
      records.push({
        route: path,
        viewport: viewport.name,
        apiMethods,
        apiRequests: routeApiRequests,
        fontsLoaded,
        ...layout,
      });
      await page.screenshot({
        path: testInfo.outputPath(`atlasops-${path === "/" ? "overview" : path.slice(1)}-${viewport.name}.png`),
        fullPage: true,
      });
    }
  }

  await page.setViewportSize({ width: 390, height: 844 });
  const mobileMenuRequestOffset = methods.length;
  const mobileMenuApiRequestOffset = apiRequests.length;
  await page.goto("/");
  const more = page.getByRole("button", { name: "More" });
  await more.click();
  const additionalNavigation = page.getByRole("region", { name: "Additional navigation" });
  await expect(additionalNavigation).toBeVisible();
  const mobileMenuFontsLoaded = await inspectLoadedFonts(page);
  expect(mobileMenuFontsLoaded.geist, "mobile menu Geist Sans").toBe(true);
  expect(mobileMenuFontsLoaded.geistMono, "mobile menu Geist Mono").toBe(true);
  const mobileMenuLayout = await page.evaluate(() => ({
    documentWidth: document.documentElement.scrollWidth,
    viewportWidth: window.innerWidth,
  }));
  expect(mobileMenuLayout.documentWidth).toBeLessThanOrEqual(mobileMenuLayout.viewportWidth);
  await page.screenshot({
    path: testInfo.outputPath("atlasops-mobile-more-menu-390x844.png"),
    fullPage: true,
  });
  records.push({
    route: "mobile-more-menu",
    viewport: "390x844",
    apiMethods: methods.slice(mobileMenuRequestOffset),
    apiRequests: apiRequests.slice(mobileMenuApiRequestOffset),
    fontsLoaded: mobileMenuFontsLoaded,
    ...mobileMenuLayout,
    overlapCount: null,
  });

  expect(runtimeErrors).toEqual([]);
  expect(consoleErrors).toEqual([]);
  expect(getErrors).toEqual([]);
  expect(methods.length).toBeGreaterThan(0);
  const gitSha = execFileSync("git", ["rev-parse", "HEAD"], {
    cwd: testInfo.config.rootDir,
    encoding: "utf8",
  }).trim();
  const matrix = {
    gitSha,
    captures: records.length,
    records,
    apiMethods: methods,
    uniqueApiRequests: [...new Set(apiRequests)],
    getErrors,
    pageErrors: runtimeErrors,
    consoleErrors,
  };
  const matrixJson = JSON.stringify(matrix, null, 2);
  await writeFile(testInfo.outputPath("route-viewport-matrix.json"), matrixJson);
  await testInfo.attach("route-viewport-matrix.json", {
    body: Buffer.from(matrixJson),
    contentType: "application/json",
  });
});

test("reference public routes deep-link on desktop and mobile using GET requests only", async ({ page }, testInfo) => {
  const methods: string[] = [];
  const errors: string[] = [];
  page.on("request", (request) => methods.push(request.method()));
  page.on("pageerror", (error) => errors.push(error.message));

  for (const viewport of [
    { name: "desktop", width: 1440, height: 900 },
    { name: "mobile", width: 390, height: 844 },
  ]) {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    for (const route of referenceRoutes) {
      const response = await page.goto(route.path);
      expect(response?.status(), route.path).toBe(200);
      await expect(page.getByRole("heading", { level: 1, name: route.heading })).toBeVisible();
      await expect(page.locator(".app-frame")).toHaveCount(0);
      if (route.id === "home") {
        await expect(page.locator(".content-architecture-reference")).toBeVisible();
      } else {
        await expect(page.locator(".reference-secondary-page")).toBeVisible();
      }
      const overflows = await page.evaluate(() =>
        document.documentElement.scrollWidth > window.innerWidth
        || document.body.scrollWidth > window.innerWidth
      );
      expect(overflows, `${route.path} overflows at ${viewport.width}px`).toBe(false);
      await page.screenshot({
        path: testInfo.outputPath(`reference-${viewport.name}-${route.id}.png`),
      });
    }
  }
  expect(methods.length).toBeGreaterThan(0);
  expect(methods.every((method) => method === "GET")).toBe(true);
  expect(errors).toEqual([]);
});

test("reference home keeps local links, pricing anchor, newsletter dialog and route navigation", async ({ page }) => {
  const methods: string[] = [];
  page.on("request", (request) => methods.push(request.method()));
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/reference");

  await expect(page.getByRole("heading", { level: 1, name: /The stack agents/ })).toBeVisible();
  const homeLinks = page.locator('a[aria-label="Home"]');
  await expect(homeLinks).toHaveCount(2);
  expect(await homeLinks.evaluateAll((links) => links.every(
    (link) => link.getAttribute("href") === "/reference",
  ))).toBe(true);
  await expect(page.locator(".reference-footer-logo")).toHaveAttribute("href", "/reference");

  await page.getByRole("link", { name: "The repo", exact: true }).click();
  await expect(page).toHaveURL(/\/reference#the-repo$/);
  await expect(page.getByText("This is the actual repo.", { exact: true })).toBeVisible();

  await page.getByRole("link", { name: "Pricing", exact: true }).click();
  await expect(page).toHaveURL(/\/reference#pricing$/);
  await expect(page.locator("#pricing")).toBeInViewport();

  const newsletterDialog = page.getByRole("dialog", { name: "Not buying today? Stay close." });
  await expect(newsletterDialog).toBeVisible({ timeout: 20_000 });
  await page.keyboard.press("Escape");
  await expect(newsletterDialog).toHaveCount(0);

  await page.getByRole("navigation", { name: "Primary navigation" })
    .getByRole("link", { name: "Blog", exact: true }).click();
  await expect(page).toHaveURL(/\/blog$/);
  await expect(page.getByRole("heading", { level: 1, name: "Knowledge Hub" })).toBeVisible();
  expect(methods.every((method) => method === "GET")).toBe(true);
});

test("unknown server paths return 404 instead of the frontend shell", async ({ page }) => {
  for (const path of [
    "/blog/not-an-article",
    "/artifacts/evidence/stage7/free-t4-v17/RESULT.json",
    "/%2e%2e/artifacts/evidence/stage7/free-t4-v17/RESULT.json",
  ]) {
    const response = await page.goto(path);
    expect(response?.status(), path).toBe(404);
    await expect(page.locator("#root")).toHaveCount(0);
  }
});

test("matched comparison and controlled RL findings display canonical API values", async ({ page }) => {
  await page.goto("/#/evaluations");
  await expect(page.locator(".validation-metric").filter({ hasText: "Base F1" }).locator("strong"))
    .toHaveText("0.16875");
  await expect(page.locator(".validation-metric").filter({ hasText: "SFT F1" }).locator("strong"))
    .toHaveText("0.15935");
  await expect(page.locator(".validation-metric").filter({ hasText: "Delta" }).locator("strong"))
    .toHaveText("-0.00940");
  await expect(page.getByText("No diagnostic improvement observed")).toBeVisible();
  const schemaCounts = page.locator(".schema-compare div strong");
  await expect(schemaCounts).toHaveCount(2);
  await expect(schemaCounts.nth(0)).toHaveText("6 / 6");
  await expect(schemaCounts.nth(1)).toHaveText("6 / 6");

  const rl = page.locator(".result-section--rl");
  await expect(rl.locator(".rl-outcome-number strong")).toHaveText("4");
  await expect(rl.locator(".rl-fact").filter({ hasText: "Malformed or blocked" }).locator("strong")).toHaveText("4");
  await expect(rl.locator(".rl-fact").filter({ hasText: "Reward per blocked action" }).locator("strong")).toHaveText("-1");
  await expect(rl.locator(".rl-fact").filter({ hasText: /^Reward-driven advantage groups/ }).locator("strong")).toHaveText("0");
  await expect(rl.locator(".rl-fact").filter({ hasText: "Groups with zero reward-driven advantages" }).locator("strong")).toHaveText("2");
  await expect(rl).toContainText("No acceptable SFT+GRPO checkpoint was saved.");

  const aligned = page.locator(".result-section--aligned");
  await expect(aligned.locator(".admissibility strong")).toHaveText("0/8");
  await expect(aligned.locator(".rl-fact").filter({ hasText: "Optimizer steps" }).locator("strong")).toHaveText("0");
  await expect(aligned.locator(".rl-fact").filter({ hasText: "LoRA tensors checked" }).locator("strong")).toHaveText("392");
  await expect(aligned.locator(".rl-fact").filter({ hasText: "Hashes unchanged" }).locator("strong")).toHaveText("Yes");
  await aligned.getByRole("button", { name: "Interpretation limits" }).click();
  await expect(aligned).toContainText("does not estimate population probability");
});

test("G4 chronology separates the 018 marker from the latest completed negative 017", async ({ page }) => {
  await page.goto("/#/incidents");
  const rows = page.locator(".timeline-item");
  await expect(rows).toHaveCount(4);
  await expect(rows.nth(0)).toContainText("015");
  await expect(rows.nth(0)).toContainText("INCONCLUSIVE");
  await expect(rows.nth(1)).toContainText("016");
  await expect(rows.nth(1)).toContainText("PRE-FAULT ABORT / NON-RESULT");
  await expect(rows.nth(2)).toContainText("017");
  await expect(rows.nth(2)).toContainText("COMPLETED NEGATIVE");
  await expect(rows.nth(2)).toContainText("Last completed attempt");
  await expect(rows.nth(3)).toContainText("018");
  await expect(rows.nth(3)).toContainText("COMPLETED MARKER / OUTCOME UNAVAILABLE");
  await expect(rows.nth(3)).toContainText("Attempt marker only; not an incident outcome");
  await expect(rows.nth(3)).not.toContainText("Last completed attempt");
  await rows.nth(3).getByRole("button", { name: "Source and scoring details" }).click();
  await expect(rows.nth(3)).toContainText(
    "50f708112e3f2155922f8aade75e3e30169652eb244f97d11196ebce176bc208",
  );
  await expect(rows.nth(3)).toContainText("docs/EVIDENCE_INDEX.md");
  await expect(page.locator(".gate-status")).toContainText("NOT_PASSED");
  await expect(page.getByText("Time to resolve: Unavailable", { exact: true })).toHaveCount(4);
  await expect(page.getByText("Time to resolve: Unavailable s", { exact: true })).toHaveCount(0);
});

test("evidence filters use server tags and external rows hide raw references", async ({ page }) => {
  await page.goto("/#/evidence");
  const rows = page.locator(".evidence-row");
  await expect(rows.first()).toBeVisible();
  await page.getByRole("button", { name: "Historical", exact: true }).click();
  await expect(rows).toHaveCount(3);
  await expect(rows.first()).toContainText("HISTORICAL / NON-EMPIRICAL");

  await page.getByRole("button", { name: "External", exact: true }).click();
  await expect(rows).toHaveCount(2);
  const externalText = (await rows.allTextContents()).join(" ");
  expect(externalText).not.toMatch(/artifacts\/evidence\/stage4|[a-f0-9]{64}/i);
  await expect(rows.first()).toContainText("external");

  await page.getByRole("button", { name: "All", exact: true }).click();
  await rows.first().getByRole("button", { name: "Source details" }).click();
  await expect(rows.first().getByText("SHA-256", { exact: true })).toBeVisible();
});

test("narrow navigation closes with Escape and outside click", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  const more = page.getByRole("button", { name: "More" });
  await more.click();
  await expect(page.getByRole("region", { name: "Additional navigation" })).toBeVisible();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("region", { name: "Additional navigation" }).getByRole("link").first()).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("region", { name: "Additional navigation" })).toHaveCount(0);
  await more.click();
  await page.locator(".topbar").click();
  await expect(page.getByRole("region", { name: "Additional navigation" })).toHaveCount(0);
});

test("the layout has no horizontal overflow at supported widths", async ({ page }) => {
  for (const width of [1440, 1280, 1024, 768, 390]) {
    await page.setViewportSize({ width, height: 960 });
    await page.goto("/");
    await expect(page.getByRole("heading", { level: 1, name: "AtlasOps" })).toBeVisible();
    const overflows = await page.evaluate(() =>
      document.documentElement.scrollWidth > window.innerWidth
      || document.body.scrollWidth > window.innerWidth
    );
    expect(overflows, `horizontal overflow at ${width}px`).toBe(false);
  }
});

test("all mobile routes scroll above the fixed navigation", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  for (const [, path, heading] of routes) {
    await page.goto(`/#${path}`);
    await expect(page.getByRole("heading", { level: 1, name: heading })).toBeVisible();
    await expect(page.getByText("Snapshot available", { exact: true })).toBeVisible();
    const main = page.locator(".main-content");
    await main.evaluate((element) => { element.scrollTop = element.scrollHeight; });
    const bounds = await page.evaluate(() => {
      const main = document.querySelector(".main-content")!;
      const last = document.querySelector(".page-stack")!.lastElementChild!;
      const nav = document.querySelector(".mobile-nav")!;
      return {
        mainBottom: main.getBoundingClientRect().bottom,
        lastBottom: last.getBoundingClientRect().bottom,
        navTop: nav.getBoundingClientRect().top,
        navPosition: getComputedStyle(nav).position,
      };
    });
    expect(bounds.navPosition).toBe("fixed");
    expect(bounds.mainBottom, path).toBeLessThanOrEqual(bounds.navTop + 1);
    expect(bounds.lastBottom, path).toBeLessThanOrEqual(bounds.navTop - 12);
  }
});

test("meaningful metadata stays readable and mobile architecture stays compact", async ({ page }) => {
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/");
    await expect(page.getByText("Snapshot available", { exact: true })).toBeVisible();
    await expect(page.locator(".architecture-flow")).toBeVisible();
    const overview = await page.evaluate(() => ({
      architectureHeight: document.querySelector(".architecture-flow")!.getBoundingClientRect().height,
      metadataSizes: [...document.querySelectorAll(
        ".architecture-kind, .architecture-agent-label, .architecture-caption, .hero-source, .api-indicator"
      )].map((element) => Number.parseFloat(getComputedStyle(element).fontSize)),
    }));
    expect(overview.metadataSizes.every((size) => size >= 12)).toBe(true);
    if (width === 390) expect(overview.architectureHeight).toBeLessThan(540);
    await page.goto("/#/evidence");
    await expect(page.locator(".evidence-row").first()).toBeVisible();
    const sizes = await page.locator(".evidence-row").first().locator(
      ".evidence-row-title strong, .badge, .evidence-row-meta, .evidence-row-meta code"
    ).evaluateAll((elements) => elements.map((element) => Number.parseFloat(getComputedStyle(element).fontSize)));
    expect(sizes.every((size) => size >= 12)).toBe(true);
  }
});

test("reduced-motion preference is honored without losing page content", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1, name: "AtlasOps" })).toBeVisible();
  await expect.poll(() => page.evaluate(
    () => window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  )).toBe(true);
  const transitionMs = await page.locator(".architecture-agent-cluster").evaluate((element) => {
    const value = getComputedStyle(element).transitionDuration;
    return value.endsWith("ms") ? Number.parseFloat(value) : Number.parseFloat(value) * 1000;
  });
  expect(transitionMs).toBeLessThan(0.1);
});

test("the presentation makes no non-GET API requests", async ({ page }) => {
  const apiMethods: string[] = [];
  page.on("request", (request) => {
    if (new URL(request.url()).pathname.startsWith("/api/")) apiMethods.push(request.method());
  });
  await page.goto("/");
  await page.goto("/#/incidents");
  await page.getByRole("button", { name: /Browse preserved records/ }).click();
  await page.locator(".historical-attempt .accordion-trigger").first().click();
  await expect(page.getByText("Reading this historical record…")).toHaveCount(0);
  expect(apiMethods.length).toBeGreaterThan(0);
  expect(apiMethods.every((method) => method === "GET")).toBe(true);
});

async function reachApproval(page: import("@playwright/test").Page) {
  await page.getByRole("button", { name: "Start rehearsal", exact: true }).click();
  for (let i = 0; i < 3; i++) await page.getByRole("button", { name: "Next stage", exact: true }).click();
  await expect(page.getByRole("region", { name: "Simulated P1 decision" })).toBeVisible();
}

test("rehearsal pauses for approval, completes, inspects prior stages and exports labels", async ({ page }) => {
  const methods: string[] = [];
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("request", (request) => {
    if (new URL(request.url()).pathname.startsWith("/api/")) methods.push(request.method());
  });
  await page.goto("/#/demo");
  await expect(page.getByText("SYNTHETIC / NON-LIVE / NON-EMPIRICAL", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Start rehearsal", exact: true }).click();
  await page.getByRole("button", { name: "Play", exact: true }).click();
  await expect(page.getByRole("region", { name: "Simulated P1 decision" })).toBeVisible({ timeout: 10000 });
  await expect(page.getByRole("button", { name: "Next stage", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Pause", exact: true })).toBeDisabled();
  await expect(page.locator(".demo-log li")).toHaveCount(4);
  await page.getByRole("button", { name: "Approve simulation", exact: true }).click();
  await page.getByRole("button", { name: "Next stage", exact: true }).click();
  await page.getByRole("button", { name: "Next stage", exact: true }).click();
  await expect(page.getByText("SIMULATED RECOVERY", { exact: true })).toBeVisible();
  await expect(page.locator(".demo-log li")).toHaveCount(7);
  await page.locator(".demo-stage").filter({ hasText: "Triage" }).click();
  await expect(page.locator(".demo-inspector h3")).toHaveText("Triage");
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download simulated transcript" }).click();
  const download = await downloadPromise;
  const file = await download.path();
  const fs = await import("node:fs/promises");
  const exported = JSON.parse(await fs.readFile(file!, "utf-8"));
  expect(exported.experimental_evidence).toBe(false);
  expect(exported.real_tool_executed).toBe(false);
  expect(exported.classification).toContain("NON-EMPIRICAL");
  expect(exported.entries).toHaveLength(7);
  expect(methods.every((method) => method === "GET")).toBe(true);
  expect(errors).toEqual([]);
});

test("rejected and timed-out approval never reach remediation", async ({ page }) => {
  await page.goto("/#/demo");
  for (const decision of ["Reject", "Simulate timeout"]) {
    await reachApproval(page);
    await page.getByRole("button", { name: decision, exact: true }).click();
    await expect(page.getByText("SIMULATED REMEDIATION BLOCKED", { exact: true })).toBeVisible();
    await expect(page.locator(".demo-stage.is-skipped")).toHaveCount(2);
    await expect(page.locator(".demo-log li")).toHaveCount(5);
    await expect(page.locator(".demo-log")).not.toContainText("Fixture action accepted");
    await page.getByRole("button", { name: "Reset rehearsal" }).click();
  }
});

test("tool success with failed verification stays unresolved on a narrow screen", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/#/demo");
  await page.getByLabel("Scenario", { exact: true }).selectOption("single_fault/sf-004");
  await page.getByLabel("Simulated verifier observation").selectOption("unhealthy");
  await reachApproval(page);
  await page.getByRole("button", { name: "Approve simulation", exact: true }).click();
  await page.getByRole("button", { name: "Next stage", exact: true }).click();
  await page.getByRole("button", { name: "Next stage", exact: true }).click();
  await expect(page.getByText("SIMULATED UNRESOLVED INCIDENT", { exact: true })).toBeVisible();
  await expect(page.locator(".demo-outcome")).toContainText("despite the simulated tool success");
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
  await page.getByRole("button", { name: "Reset rehearsal" }).click();
  await expect(page.getByText("No rehearsal started.", { exact: true })).toBeVisible();
});

test("missing rehearsal fixtures do not invent a replacement run", async ({ page }) => {
  await page.route("**/api/rehearsal", (route) => route.fulfill({ status: 503, body: "{}" }));
  await page.goto("/#/demo");
  await expect(page.getByRole("alert")).toContainText("Rehearsal fixtures unavailable");
  await expect(page.getByRole("button", { name: "Start rehearsal", exact: true })).toBeDisabled();
  await expect(page.locator(".demo-log")).toHaveCount(0);
});

test("manual pause stops playback and desktop/mobile rehearsal views are captured", async ({ page }, testInfo) => {
  await page.goto("/#/demo");
  await page.getByRole("button", { name: "Start rehearsal", exact: true }).click();
  await page.getByRole("button", { name: "Play", exact: true }).click();
  await page.getByRole("button", { name: "Pause", exact: true }).click();
  await page.waitForTimeout(1700);
  await expect(page.locator(".demo-log li")).toHaveCount(1);
  for (let i = 0; i < 3; i++) await page.getByRole("button", { name: "Next stage", exact: true }).click();
  await page.screenshot({ path: testInfo.outputPath("desktop-approval.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: testInfo.outputPath("mobile-approval.png"), fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
});

test("live observations stay separate from the simulation and refresh failure is unavailable", async ({ page }) => {
  await page.route("**/api/live-status", (route) => route.fulfill({
    json: { enabled: true, observed_at: "2026-10-07T03:00:00Z", services: [
      { id: "coordinator", label: "Coordinator process", available: true, detail: "Process health only; model execution not tested" },
      { id: "inference", label: "Cached model server", available: false, detail: "Service unavailable" },
    ] },
  }));
  await page.goto("/#/demo");
  const live = page.getByRole("region", { name: "Live service observations" });
  await expect(live).toContainText("Responding");
  await expect(live).toContainText("Unavailable");
  await expect(live).toContainText("not agent execution or incident-resolution evidence");
  await expect(page.getByText("SYNTHETIC / NON-LIVE / NON-EMPIRICAL", { exact: true })).toBeVisible();
  await page.route("**/api/live-status", (route) => route.fulfill({ status: 503 }));
  await page.getByRole("button", { name: "Refresh live observations" }).click();
  await expect(live.getByRole("alert")).toContainText("No substitute health result");
  await expect(live.getByText("Responding", { exact: true })).toHaveCount(0);
});

test("governed capture displays unreserved startup failure without a recovery claim", async ({ page }) => {
  await page.route("**/api/incident-monitor", (route) => route.fulfill({ json: {
    enabled: true, available: true, experiment_id: "EXP-STAGE4-SF002-018",
    observed_at: "2026-10-07T06:00:00Z", status: "STARTUP_FAILED / NOT_RESERVED",
    process_running: false, reserved: false, attempt_state: null,
    failure: "bridge_transport_failure", gate_g4_pass: null, env_resolved: null,
    cleanup_verified_zero: null, approval: null, severity: null, services: [],
    phases: [{ id: "agents", label: "Agent workflow", observed: false }],
    actions: [], proposal: null, sources: [{ name: "Process exit", sha256: "a".repeat(64) }],
  } }));
  await page.goto("/#/demo");
  const monitor = page.getByRole("region", { name: "Governed incident monitor" });
  await expect(monitor).toContainText("STARTUP_FAILED / NOT_RESERVED");
  await expect(monitor).toContainText("bridge_transport_failure");
  await expect(monitor).toContainText("RECORDED / NOT A SIMULATION");
  await expect(monitor.locator("dl div").filter({ hasText: "Recorded G4 pass" }).locator("dd")).toHaveText("Unavailable");
  await expect(monitor.locator("dl div").filter({ hasText: "Attempt reserved" }).locator("dd")).toHaveText("No");
  await expect(monitor).toContainText("Not recorded");
  await expect(page.getByText("SYNTHETIC / NON-LIVE / NON-EMPIRICAL", { exact: true })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
  await page.route("**/api/incident-monitor", (route) => route.fulfill({ status: 503 }));
  await monitor.getByRole("button", { name: "Refresh incident capture" }).click();
  await expect(monitor.getByRole("alert")).toContainText("Previous observations are not shown");
  await expect(monitor.getByText("STARTUP_FAILED / NOT_RESERVED", { exact: true })).toHaveCount(0);
});

test("synthetic governed negative remains negative despite tool success and cleanup", async ({ page }) => {
  await page.route("**/api/incident-monitor", (route) => route.fulfill({ json: {
    enabled: true, available: true, experiment_id: "EXP-STAGE4-SF002-999",
    observed_at: "2026-10-07T06:00:00Z", status: "RECORDED_NEGATIVE",
    process_running: false, reserved: true, attempt_state: "COMPLETED",
    failure: null, gate_g4_pass: false, env_resolved: false,
    cleanup_verified_zero: true, approval: "timeout", severity: "P1", services: ["paymentservice"],
    policy_block: "invalid_action",
    phases: [{ id: "agents", label: "Agent workflow", observed: true }],
    actions: [{ tool: "chaos_stop_experiment", target: "sf-002-paymentservice-cpu", namespace: "chaos-mesh", success: true }],
    proposal: null, sources: [],
  } }));
  await page.goto("/#/demo");
  const monitor = page.getByRole("region", { name: "Governed incident monitor" });
  await expect(monitor).toContainText("RECORDED_NEGATIVE");
  await expect(monitor).toContainText("Recorded policy block: invalid_action");
  await expect(monitor.locator("dl div").filter({ hasText: "Environment resolved" }).locator("dd")).toHaveText("No");
  await expect(monitor.locator("dl div").filter({ hasText: "Cleanup verified zero Chaos" }).locator("dd")).toHaveText("Yes");
  await expect(monitor.locator("dl div").filter({ hasText: "Recorded P1 decision" }).locator("dd")).toHaveText("timeout");
  await expect(monitor.getByRole("region", { name: "Recorded tool outcomes" })).toContainText("Tool success: Yes");
});

test("completed attempt marker is visible without inferring incident outcome", async ({ page }) => {
  await page.route("**/api/incident-monitor", (route) => route.fulfill({ json: {
    enabled: true, available: true, experiment_id: "EXP-STAGE4-SF002-018",
    observed_at: "2026-10-07T06:00:00Z",
    status: "COMPLETED MARKER / OUTCOME UNAVAILABLE",
    process_running: false, reserved: true, attempt_state: "COMPLETED",
    failure: null, gate_g4_pass: null, env_resolved: null,
    cleanup_verified_zero: null, approval: null, severity: null, services: [],
    phases: [], actions: [], proposal: null,
    sources: [{
      name: "Attempt marker (accounting only; not an outcome)",
      sha256: "50f708112e3f2155922f8aade75e3e30169652eb244f97d11196ebce176bc208",
    }],
  } }));
  await page.goto("/#/demo");
  const monitor = page.getByRole("region", { name: "Governed incident monitor" });
  await expect(monitor).toContainText("COMPLETED MARKER / OUTCOME UNAVAILABLE");
  await expect(monitor).toContainText("ATTEMPT MARKER ONLY");
  await expect(monitor.getByText("RECORDED / NOT A SIMULATION", { exact: true })).toHaveCount(0);
  await expect(monitor).not.toContainText("RECORDED_NEGATIVE");
  await expect(monitor).not.toContainText("RECORDED_PASS");
  await expect(monitor.locator("dl div").filter({ hasText: "Attempt reserved" }).locator("dd"))
    .toHaveText("Yes");
  await expect(monitor.locator("dl div").filter({ hasText: "Environment resolved" }).locator("dd"))
    .toHaveText("Unavailable");
  await expect(monitor.locator("dl div").filter({ hasText: "Recorded G4 pass" }).locator("dd"))
    .toHaveText("Unavailable");
  await expect(monitor.locator("dl div").filter({ hasText: "Cleanup verified zero Chaos" }).locator("dd"))
    .toHaveText("Unavailable");
  await expect(monitor).toContainText(
    "50f708112e3f2155922f8aade75e3e30169652eb244f97d11196ebce176bc208",
  );
});

test("operator blocks exhausted launches and submits only the displayed exact action", async ({ page }, testInfo) => {
  const proposal = {
    token: "apr-test", action_digest: "c".repeat(64), incident_id: "incident-019", severity: "P1",
    action: { tool: "chaos_stop_experiment", arguments: { name: "sf-002-paymentservice-cpu", namespace: "chaos-mesh" } },
    operator_scope: { kube_context: "kind-atlasops-local", scenario_id: "single_fault/sf-002" },
  };
  let pending: typeof proposal[] = [];
  await page.route("**/api/operator", (route) => route.fulfill({ json: {
    enabled: true, csrf_token: "test-session", source_sha: "a".repeat(40),
    protocol_fingerprint: "b".repeat(64), experiment_id: "EXP-STAGE4-SF002-019",
    kube_context: "kind-atlasops-local", scenario_id: "single_fault/sf-002",
    readiness: { can_start: false, blockers: ["protocol_attempt_budget_exhausted"],
      attempts_used: 2, attempt_limit: 2, runtime_qualified: false },
    pending, channel_error: null, capture: null,
  } }));
  const decisions: unknown[] = [];
  await page.route("**/api/operator/decision", (route) => {
    decisions.push(route.request().postDataJSON());
    expect(route.request().headers()["x-atlasops-operator"]).toBe("test-session");
    pending = [];
    return route.fulfill({ json: { decision: "approved" } });
  });
  await page.goto("/#/demo");
  const panel = page.getByRole("region", { name: "Live incident controls" });
  await expect(panel).toContainText("protocol attempt budget exhausted");
  await expect(panel.getByRole("button", { name: "Start governed SF002 run" })).toBeDisabled();
  pending = [proposal];
  await panel.getByRole("button", { name: "Refresh operator status" }).click();
  await expect(panel.getByRole("region", { name: "Exact live action approval" })).toContainText("sf-002-paymentservice-cpu");
  await page.screenshot({ path: testInfo.outputPath("operator-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: testInfo.outputPath("operator-mobile.png"), fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
  await panel.getByRole("button", { name: "Approve exact action" }).click();
  expect(decisions).toEqual([{ token: proposal.token, action_digest: proposal.action_digest, decision: "approved" }]);
  await expect(panel.getByRole("region", { name: "Exact live action approval" })).toHaveCount(0);
});

test("an active runner with an unavailable channel never looks like an empty approval queue", async ({ page }) => {
  await page.route("**/api/operator", (route) => route.fulfill({ json: {
    enabled: true, csrf_token: "test-session", source_sha: "a".repeat(40),
    protocol_fingerprint: "b".repeat(64), experiment_id: "EXP-STAGE4-SF002-019",
    kube_context: "kind-atlasops-local", scenario_id: "single_fault/sf-002",
    readiness: { can_start: false, blockers: ["server_launch_already_used"],
      attempts_used: 1, attempt_limit: 2, runtime_qualified: false },
    pending: [], channel_error: "approval_channel_unavailable", events: null,
    capture: { status: "RUNNING", process_running: true, phases: [],
      env_resolved: null, gate_g4_pass: null, cleanup_verified_zero: null,
      policy_block: null, comms_status: null },
  } }));
  await page.goto("/#/demo");
  const panel = page.getByRole("region", { name: "Live incident controls" });
  await expect(panel.getByRole("alert")).toContainText("Approval channel unavailable");
  await expect(panel).toContainText("Agent activity unavailable");
  await expect(panel.getByRole("button", { name: "Approve exact action" })).toHaveCount(0);
  await expect(panel.locator("dl div").filter({ hasText: "Environment resolved" }).locator("dd")).toHaveText("Unavailable");
});

test("operator polling clears a transient status error after a successful read", async ({ page }) => {
  let failed = true;
  await page.route("**/api/operator", (route) => route.fulfill(failed ? { status: 503 } : { json: {
    enabled: true, csrf_token: "test-session", source_sha: "a".repeat(40),
    protocol_fingerprint: "b".repeat(64), experiment_id: "EXP-STAGE4-SF002-019",
    kube_context: "kind-atlasops-local", scenario_id: "single_fault/sf-002",
    readiness: { can_start: false, blockers: ["protocol_attempt_budget_exhausted"],
      attempts_used: 2, attempt_limit: 2, runtime_qualified: false },
    pending: [], channel_error: null, events: null, capture: null,
  } }));
  await page.goto("/#/demo");
  const panel = page.getByRole("region", { name: "Live incident controls" });
  await expect(panel.getByRole("alert")).toContainText("Operator status unavailable");
  failed = false;
  await expect(panel).toContainText("protocol attempt budget exhausted", { timeout: 10000 });
  await expect(panel.getByRole("alert")).toHaveCount(0);
});
