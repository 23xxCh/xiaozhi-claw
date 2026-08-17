import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const controlApiUrl = process.env.E2E_CONTROL_API_URL ?? "http://127.0.0.1:8000";

async function authenticatePilot(page: Page) {
  const response = await page.request.post(`${controlApiUrl}/v1/auth/dev-login`, {
    data: { openid: process.env.E2E_DEV_OPENID ?? "lan-28:84:85:4a:3d:b8", adult_confirmed: true },
  });
  expect(response.ok()).toBeTruthy();
  await page.goto("/console");
  await expect(page.getByRole("heading", { level: 1, name: /今天想聊点什么/ })).toBeVisible();
}

test("unauthenticated customer route returns to the same page after login", async ({ page }) => {
  await page.goto("/console/devices");
  await expect(page).toHaveURL(/\/login\?next=%2Fconsole%2Fdevices/);
  await expect(page.getByRole("button", { name: /微信|扫码/ })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "获取验证码" })).toBeVisible();
  await page.route("**/v1/auth/email/request-code", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ expires_in: 600, resend_after: 60, debug_code: "123456" }),
  }));
  await page.route("**/v1/auth/email/verify-code", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ access_token: "test", token_type: "bearer", agreements_complete: true }),
  }));
  await page.route("**/v1/devices", (route) => route.fulfill({ status: 200, contentType: "application/json", body: "[]" }));
  await page.route("**/v1/agents", (route) => route.fulfill({ status: 200, contentType: "application/json", body: "[]" }));
  await page.route("**/v1/profiles", (route) => route.fulfill({ status: 200, contentType: "application/json", body: "[]" }));
  await page.getByLabel("邮箱地址").fill("owner@example.com");
  await page.getByRole("button", { name: "获取验证码" }).click();
  await expect(page.getByLabel("6 位验证码")).toHaveValue("123456");
  await page.getByRole("button", { name: "验证并登录" }).click();
  await expect(page).toHaveURL(/\/console\/devices$/);
  await expect(page.getByRole("heading", { level: 1, name: "设备" })).toBeVisible();
});

test("customer console has no horizontal overflow at required viewports", async ({ page }) => {
  await authenticatePilot(page);
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
  await authenticatePilot(page);
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
    if (box) {
      const label = await locator.evaluate((element) => {
        const named = element.getAttribute("aria-label") || element.textContent?.trim();
        return `${element.tagName.toLowerCase()} ${named || "(unnamed)"}`;
      });
      // This is injected by `next dev`, not by the customer application.
      if (label === "button Open Next.js Dev Tools") continue;
      expect(Math.min(box.width, box.height), label).toBeGreaterThanOrEqual(44);
    }
  }
});

test("theme choice persists in a cookie", async ({ page }) => {
  await authenticatePilot(page);
  const root = page.locator("html");
  const before = await root.getAttribute("data-theme");
  await page.getByRole("button", { name: "切换浅色或深色模式" }).click();
  const expected = before === "dark" ? "light" : "dark";
  await expect(root).toHaveAttribute("data-theme", expected);
  await page.reload();
  await expect(root).toHaveAttribute("data-theme", expected);
});

test("customer navigation hides operations and API failures stay in Chinese", async ({ page }) => {
  await authenticatePilot(page);
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
