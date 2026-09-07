import { expect, test } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  await page.route("**/v1/onboarding/status?*", (route) => route.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({ first_conversation_complete: false }),
  }));
});

test("claim fragment survives login without entering an access log", async ({ page }) => {
  let authenticated = false;
  let confirmedCode = "";

  await page.route("**/v1/auth/me", (route) => route.fulfill({
    status: authenticated ? 200 : 401,
    contentType: "application/json",
    body: authenticated
      ? JSON.stringify({ adult_confirmed: true, agreements_complete: true })
      : JSON.stringify({ code: "UNAUTHORIZED", message: "请先登录" }),
  }));
  await page.route("**/v1/auth/email/request-code", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ expires_in: 600, resend_after: 60, debug_code: "654321" }),
  }));
  await page.route("**/v1/auth/email/verify-code", (route) => {
    authenticated = true;
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ agreements_complete: true }),
    });
  });
  await page.route("**/v1/claims/confirm", async (route) => {
    confirmedCode = (await route.request().postDataJSON()).claim_code;
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ id: "device-1" }),
    });
  });
  await page.route("**/v1/devices", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify([{ id: "device-1", online: true }]),
  }));

  await page.goto("/claim#code=123456");
  await expect(page).toHaveURL(/\/login\?next=%2Fclaim$/);
  expect(await page.evaluate(() => sessionStorage.getItem("hensun_claim_code"))).toBe("123456");
  await page.getByLabel("邮箱地址").fill("owner@example.com");
  await page.getByRole("button", { name: "获取验证码" }).click();
  await page.getByRole("button", { name: "验证并登录" }).click();

  await expect(page).toHaveURL(/\/claim$/);
  await expect(page.getByText("设备在线，可以聊天")).toBeVisible();
  await expect(page.getByText("请说：你好小灿")).toBeVisible();
  expect(confirmedCode).toBe("123456");
  expect(await page.evaluate(() => sessionStorage.getItem("hensun_claim_code"))).toBeNull();
});

test("claim stays bound while offline and refreshes the real online state", async ({ page }) => {
  let online = false;
  let claimRequests = 0;
  await page.route("**/v1/auth/me", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ agreements_complete: true }),
  }));
  await page.route("**/v1/claims/confirm", (route) => {
    claimRequests += 1;
    return route.fulfill({
      status: 200, contentType: "application/json", body: JSON.stringify({ id: "device-1" }),
    });
  });
  await page.route("**/v1/devices", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify([{ id: "device-1", online }]),
  }));

  await page.goto("/claim#code=123456");
  await expect(page.getByText("设备已绑定，等待设备上线")).toBeVisible();
  await expect(page.getByText("设备在线，可以聊天")).toHaveCount(0);
  await expect(page.getByText("请说：你好小灿")).toHaveCount(0);
  expect(await page.evaluate(() => sessionStorage.getItem("hensun_claim_code"))).toBeNull();

  online = true;
  await page.getByRole("button", { name: "刷新设备状态" }).click();
  await expect(page.getByText("设备在线，可以聊天")).toBeVisible();
  await expect(page.getByText("请说：你好小灿")).toBeVisible();
  expect(claimRequests).toBe(1);
});

test("claimed device survives refresh without reclaiming and is checked against this account", async ({ page }) => {
  let claims = 0;
  let owned = true;
  await page.route("**/v1/auth/me", (route) => route.fulfill({ status: 200, contentType: "application/json", body: '{"agreements_complete":true}' }));
  await page.route("**/v1/claims/confirm", (route) => {
    claims += 1;
    return route.fulfill({ status: 200, contentType: "application/json", body: '{"id":"device-refresh"}' });
  });
  await page.route("**/v1/devices", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(owned ? [{ id: "device-refresh", online: false }] : []) }));
  await page.goto("/claim#code=123456");
  await expect(page.getByText("设备已绑定，等待设备上线")).toBeVisible();
  await page.reload();
  await expect(page.getByText("设备已绑定，等待设备上线")).toBeVisible();
  expect(claims).toBe(1);
  expect(await page.evaluate(() => sessionStorage.getItem("hensun_claim_code"))).toBeNull();
  owned = false;
  await page.reload();
  await expect(page.getByText("当前账户没有这台设备，请重新扫码或查看我的设备")).toBeVisible();
  expect(await page.evaluate(() => sessionStorage.getItem("hensun_claim_device_id"))).toBeNull();
  expect(claims).toBe(1);
});

for (const [status, message] of [[403, "当前操作不被允许"], [410, "绑定码已过期"], [409, "设备已被认领"]] as const) {
  test(`claim HTTP ${status} shows an actionable error and permits one explicit retry`, async ({ page }) => {
    let calls = 0;
    await page.route("**/v1/auth/me", (route) => route.fulfill({ status: 200, contentType: "application/json", body: '{"agreements_complete":true}' }));
    await page.route("**/v1/claims/confirm", (route) => {
      calls += 1;
      return route.fulfill({ status, contentType: "application/json", body: JSON.stringify({ code: "REJECTED", message }) });
    });
    await page.goto("/claim#code=123456");
    await expect(page.getByRole("main").getByRole("alert")).toContainText(message);
    await expect(page.getByText("正在确认登录与设备绑定…")).toHaveCount(0);
    expect(calls).toBe(1);
    await page.getByRole("button", { name: "重试绑定" }).click();
    await expect.poll(() => calls).toBe(2);
    await expect(page.getByRole("main").getByRole("alert")).toContainText(message);
    expect(new URL(page.url()).search).not.toContain("123456");
  });
}

test("a lost claim response is not automatically posted again on refresh", async ({ page }) => {
  let calls = 0;
  await page.route("**/v1/auth/me", (route) => route.fulfill({ status: 200, contentType: "application/json", body: '{"agreements_complete":true}' }));
  await page.route("**/v1/claims/confirm", (route) => { calls += 1; return route.abort("failed"); });
  await page.goto("/claim#code=123456");
  await expect(page.getByRole("main").getByRole("alert")).toContainText("无法连接服务");
  await page.reload();
  await expect(page.getByRole("main").getByRole("alert")).toContainText("上次绑定结果尚未确认");
  await expect(page.getByRole("link", { name: "查看我的设备" })).toBeVisible();
  expect(calls).toBe(1);
});

test("blocked session storage gives a visible error instead of a loading dead end", async ({ page }) => {
  let claims = 0;
  await page.addInitScript(() => {
    Object.defineProperty(window, "sessionStorage", { configurable: true, get() { throw new DOMException("Blocked", "SecurityError"); } });
  });
  await page.route("**/v1/claims/confirm", (route) => { claims += 1; return route.abort(); });
  await page.goto("/claim#code=123456");
  await expect(page.getByRole("main").getByRole("alert")).toContainText("浏览器无法保存本次绑定进度");
  await expect(page.getByText("正在确认登录与设备绑定…")).toHaveCount(0);
  expect(claims).toBe(0);
});

test("claim waits for the real first-use agreements and resumes exactly once", async ({ page }) => {
  let agreed = false;
  let claims = 0;
  await page.route("**/v1/auth/me", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ adult_confirmed: agreed, agreements_complete: agreed }) }));
  await page.route("**/v1/auth/adult-confirmation", (route) => {
    agreed = true;
    return route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
  });
  await page.route("**/v1/claims/confirm", (route) => { claims += 1; return route.fulfill({ status: 200, contentType: "application/json", body: '{"id":"device-agreements"}' }); });
  await page.route("**/v1/devices", (route) => route.fulfill({ status: 200, contentType: "application/json", body: '[{"id":"device-agreements","online":false}]' }));
  await page.goto("/claim#code=123456");
  await expect(page).toHaveURL(/\/auth\/complete\?next=%2Fclaim$/);
  expect(claims).toBe(0);
  for (const checkbox of await page.getByRole("checkbox").all()) await checkbox.check();
  await page.getByRole("button", { name: "确认并进入控制台" }).click();
  await expect(page).toHaveURL(/\/claim$/);
  await expect(page.getByText("设备已绑定，等待设备上线")).toBeVisible();
  expect(claims).toBe(1);
});

test("status failure never reports an online device and manual refresh recovers", async ({ page }) => {
  let available = false;
  await page.route("**/v1/auth/me", (route) => route.fulfill({ status: 200, contentType: "application/json", body: '{"agreements_complete":true}' }));
  await page.route("**/v1/claims/confirm", (route) => route.fulfill({ status: 200, contentType: "application/json", body: '{"id":"device-status"}' }));
  await page.route("**/v1/devices", (route) => route.fulfill({ status: available ? 200 : 503, contentType: "application/json", body: available ? '[{"id":"device-status","online":false}]' : '{"code":"UNAVAILABLE","message":"状态暂不可用"}' }));
  await page.goto("/claim#code=123456");
  await expect(page.getByRole("main").getByRole("alert")).toContainText("状态暂不可用");
  await expect(page.getByText("设备在线，可以聊天")).toHaveCount(0);
  available = true;
  await page.getByRole("button", { name: "刷新设备状态" }).click();
  await expect(page.getByText("设备已绑定，等待设备上线")).toBeVisible();
});

test("offline polling stops at its limit and resumes only on manual refresh", async ({ page }) => {
  await page.clock.install();
  let checks = 0;
  await page.route("**/v1/auth/me", (route) => route.fulfill({ status: 200, contentType: "application/json", body: '{"agreements_complete":true}' }));
  await page.route("**/v1/claims/confirm", (route) => route.fulfill({ status: 200, contentType: "application/json", body: '{"id":"device-poll"}' }));
  await page.route("**/v1/devices", (route) => {
    checks += 1;
    return route.fulfill({ status: 200, contentType: "application/json", body: '[{"id":"device-poll","online":false}]' });
  });
  await page.goto("/claim#code=123456");
  for (let count = 1; count <= 30; count += 1) {
    await expect.poll(() => checks).toBe(count);
    await expect(page.getByRole("button", { name: "刷新设备状态" })).toBeEnabled();
    if (count < 30) await page.clock.runFor(3000);
  }
  await expect(page.getByText("自动检查已暂停。网络恢复后可以手动刷新。")).toBeVisible();
  await page.clock.runFor(12000);
  expect(checks).toBe(30);
  await page.getByRole("button", { name: "刷新设备状态" }).click();
  await page.clock.runFor(1);
  await expect.poll(() => checks).toBe(31);
});
