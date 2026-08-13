import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

async function devLogin(page: Page) {
  await page.goto("/login");
  await page.getByRole("button", { name: "本地开发登录" }).click();
  await expect(page).toHaveURL(/\/console$/);
  await expect(page.getByRole("heading", { level: 1, name: /今天想聊点什么/ })).toBeVisible();
}

test("unauthenticated customer route returns to the same page after login", async ({ page }) => {
  await page.goto("/console/devices");
  await expect(page).toHaveURL(/\/login\?next=%2Fconsole%2Fdevices/);
  await page.getByRole("button", { name: "本地开发登录" }).click();
  await expect(page).toHaveURL(/\/console\/devices$/);
});

test("customer console has no horizontal overflow at required viewports", async ({ page }) => {
  await devLogin(page);
  const routes = [
    "/console",
    "/console/devices",
    "/console/agents",
    "/console/memories",
    "/console/usage",
    "/console/account",
    "/console/family",
    "/console/more",
  ];
  const widths = [390, 430, 768, 1024, 1440];
  for (const width of widths) {
    await page.setViewportSize({ width, height: 900 });
    for (const route of routes) {
      await page.goto(route);
      await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);
      const metrics = await page.evaluate(() => ({
        viewport: window.innerWidth,
        document: document.documentElement.scrollWidth,
      }));
      expect(metrics.document, `${route} at ${width}px`).toBeLessThanOrEqual(metrics.viewport);
    }
  }
});

test("home is keyboard accessible and has no serious axe violations", async ({ page }) => {
  await devLogin(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ reducedMotion: "reduce", colorScheme: "dark" });
  await page.goto("/console");
  await page.keyboard.press("Tab");
  await expect(page.locator(":focus-visible").first()).toBeVisible();
  const results = await new AxeBuilder({ page }).analyze();
  expect(
    results.violations.filter((item) => ["serious", "critical"].includes(item.impact ?? "")),
  ).toEqual([]);
  for (const locator of await page.locator("a, button, input, select, textarea").all()) {
    if (!(await locator.isVisible())) continue;
    const box = await locator.boundingBox();
    if (box) expect(Math.min(box.width, box.height)).toBeGreaterThanOrEqual(44);
  }
});

test("theme choice persists in a cookie", async ({ page }) => {
  await devLogin(page);
  const root = page.locator("html");
  const before = await root.getAttribute("data-theme");
  await page.getByRole("button", { name: "切换浅色或深色模式" }).click();
  const expected = before === "dark" ? "light" : "dark";
  await expect(root).toHaveAttribute("data-theme", expected);
  await page.reload();
  await expect(root).toHaveAttribute("data-theme", expected);
});

test("customer navigation hides operations and API failures stay in Chinese", async ({ page }) => {
  await devLogin(page);
  await expect(page.getByRole("link", { name: /内部后台|Operations/ })).toHaveCount(0);
  await page.route("**/v1/account/usage", (route) => route.fulfill({
    status: 503,
    contentType: "application/json",
    body: JSON.stringify({ code: "SERVICE_UNAVAILABLE", message: "服务暂时不可用", request_id: "test-request" }),
  }));
  await page.goto("/console/usage");
  await expect(page.getByText("服务暂时不可用")).toBeVisible();
  await expect(page.getByRole("button", { name: "重新加载" })).toBeVisible();
  await expect(page.getByText(/Bearer|Token|Provider|API Key/)).toHaveCount(0);
});
