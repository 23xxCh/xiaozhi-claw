import { expect, test } from "@playwright/test";

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
