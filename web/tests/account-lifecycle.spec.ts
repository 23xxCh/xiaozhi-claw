import { expect, test } from "@playwright/test";

test("isolated registration persists voice settings and rejects cross-account edits", async ({ page, browser, baseURL }) => {
  test.skip(process.env.HENSUN_ISOLATED_WEB_E2E !== "1", "Requires an isolated test database and development email delivery");
  const origin = new URL(baseURL!);
  expect(origin.hostname).toBe("127.0.0.1");
  expect(origin.port).not.toBe("3000");
  const suffix = `${Date.now()}`;

  await page.goto("/login?next=%2Fconsole%2Fagents");
  await page.getByRole("button", { name: "注册账号", exact: true }).click();
  await page.getByLabel("邮箱地址").fill(`e2e-${suffix}@example.com`);
  await page.getByRole("button", { name: "获取验证码" }).click();
  await expect(page.getByLabel("6 位验证码")).toHaveValue(/^\d{6}$/);
  await page.getByRole("button", { name: "验证并注册" }).click();
  await expect(page).toHaveURL(/\/auth\/complete/);
  for (const checkbox of await page.getByRole("checkbox").all()) await checkbox.check();
  await page.getByRole("button", { name: "确认并进入控制台" }).click();
  await expect(page).toHaveURL(/\/console\/agents$/);
  await expect(page.locator("#model")).toBeVisible();

  const before = (await (await page.request.get("/v1/agents")).json())[0];
  await page.getByLabel("名称", { exact: true }).fill(`静默验收-${suffix}`);
  await page.locator("#model").selectOption("rich-chat");
  await page.locator("#voice-search").fill("Serena");
  await page.locator("#voice").selectOption("serena");
  await page.getByRole("button", { name: "保存设置" }).click();
  await expect(page.getByText("已保存。新设置从下一轮对话生效", { exact: false })).toBeVisible();
  await page.reload();
  await expect(page.getByLabel("名称", { exact: true })).toHaveValue(`静默验收-${suffix}`);
  await expect(page.locator("#model")).toHaveValue("rich-chat");
  await expect(page.locator("#voice")).toHaveValue("serena");
  const saved = (await (await page.request.get("/v1/agents")).json())[0];
  expect(saved.config_version).toBeGreaterThan(before.config_version);

  const incompatible = await page.request.patch(`/v1/agents/${saved.id}`, {
    headers: { Origin: origin.origin },
    data: { model_preset_id: "fast-chat", voice_preset_id: "doubao-vv" },
  });
  expect(incompatible.status()).toBe(422);
  const stranger = await browser.newContext({ baseURL });
  try {
    const login = await stranger.request.post("/v1/auth/dev-login", {
      data: { openid: `stranger-${suffix}`, adult_confirmed: true },
    });
    expect(login.ok()).toBeTruthy();
    const denied = await stranger.request.patch(`/v1/agents/${saved.id}`, {
      headers: { Origin: origin.origin },
      data: { name: "unauthorized" },
    });
    expect(denied.status()).toBe(404);
  } finally {
    await stranger.close();
  }
  const unchanged = (await (await page.request.get("/v1/agents")).json())[0];
  expect(unchanged).toEqual(saved);
});
