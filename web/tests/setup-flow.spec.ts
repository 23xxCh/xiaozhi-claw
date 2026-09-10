import { expect, test } from "@playwright/test";
import { safeNext } from "../lib/onboarding";

test("screen troubleshooting leads to setup and role configuration without claiming success", async ({ page }) => {
  await page.goto("/setup");
  await page.getByText("一直显示“登录服务器”或连接错误", { exact: true }).click();
  await page.getByRole("button", { name: "查看重新配网步骤" }).click();
  await expect(page.getByText("插电，等设备显示配网提示。", { exact: true })).toBeVisible();
  await page.getByText("已经绑定，想换助手或音色", { exact: true }).click();
  await expect(page.getByRole("link", { name: "配置我的助手" })).toHaveAttribute("href", "/console/agents");
  await expect(page.getByText("设备在线，可以聊天", { exact: true })).toHaveCount(0);
});

test("next stays on this site, including encoded backslash attacks", () => {
  for (const value of ["//evil.example", "/\\evil.example", "/%5cevil.example", "javascript:alert(1)", "/\n/evil.example", "/%E0%A4%A"]) {
    expect(safeNext(value)).toBe("/console");
  }
  expect(safeNext("/claim")).toBe("/claim");
  expect(safeNext("/console?device_id=sample")).toBe("/console?device_id=sample");
});

test("phone Wi-Fi help remains readable offline and never collects or submits a Wi-Fi password", async ({ page, context }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  let apiCalls = 0;
  await page.route("**/v1/**", (route) => { apiCalls += 1; return route.abort(); });
  await page.goto("/setup?mode=wifi");
  await expect(page.getByRole("heading", { name: "恢复设备联网" })).toBeVisible();
  await page.getByRole("button", { name: /设置家庭网络/ }).click();
  const ap = page.getByRole("link", { name: "打开设备配网页" });
  await expect(ap).toHaveAttribute("href", "http://192.168.4.1");
  await expect(ap).toHaveAttribute("target", "_blank");
  await expect(page.locator('input[type="password"]')).toHaveCount(0);
  await context.setOffline(true);
  await page.getByText("找不到热点或连接失败", { exact: true }).click();
  await expect(page.getByText(/手机仍连接设备热点时无法访问本网站属于正常情况/)).toBeVisible();
  await page.getByRole("button", { name: /连接设备热点/ }).click();
  await expect(page.getByText(/Xiaozhi-XXXX/)).toBeVisible();
  const width = await page.evaluate(() => ({ viewport: innerWidth, page: document.documentElement.scrollWidth }));
  expect(width.page).toBeLessThanOrEqual(width.viewport);
  await page.getByRole("button", { name: /设置家庭网络/ }).click();
  const box = await ap.boundingBox();
  expect(box!.height).toBeGreaterThanOrEqual(44);
  expect(apiCalls).toBe(0);
});

test("manual code from Devices uses the claim page and waits for real online status", async ({ page }) => {
  let claims = 0;
  await page.route("**/v1/agents", (route) => route.fulfill({ status: 200, contentType: "application/json", body: "[]" }));
  await page.route("**/v1/profiles", (route) => route.fulfill({ status: 200, contentType: "application/json", body: "[]" }));
  await page.route("**/v1/devices", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(claims ? [{ id: "new-device", online: false }] : []) }));
  await page.route("**/v1/auth/me", (route) => route.fulfill({ status: 200, contentType: "application/json", body: '{"agreements_complete":true}' }));
  await page.route("**/v1/claims/confirm", (route) => { claims += 1; return route.fulfill({ status: 200, contentType: "application/json", body: '{"id":"new-device"}' }); });
  await page.goto("/console/devices");
  await page.getByLabel("扫码失败时，输入 6 位备用码").fill("123456");
  await page.getByRole("button", { name: "确认绑定" }).click();
  await expect(page).toHaveURL(/\/claim$/);
  await expect(page.getByText("设备已绑定，等待设备上线")).toBeVisible();
  await expect(page.getByText("设备在线，可以聊天")).toHaveCount(0);
  expect(claims).toBe(1);
});

test("missing claim code provides manual entry and first-use instructions without requiring login", async ({ page }) => {
  let requests = 0;
  await page.route("**/v1/**", (route) => { requests += 1; return route.abort(); });
  await page.goto("/claim");
  await expect(page.getByLabel("设备屏幕上的 6 位备用码")).toBeVisible();
  await expect(page.getByRole("heading", { name: "先让设备连上网络" })).toBeVisible();
  expect(requests).toBe(0);
});

test("progress explicitly requests the selected second device", async ({ page }) => {
  let requestedId = "";
  await page.route("**/v1/devices", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([{ id: "device-first", online: true }, { id: "device-second", online: true }]) }));
  await page.route("**/v1/agents", (route) => route.fulfill({ status: 200, contentType: "application/json", body: "[]" }));
  await page.route("**/v1/account/usage", (route) => route.fulfill({ status: 200, contentType: "application/json", body: '{"voice_turns":10}' }));
  await page.route("**/v1/onboarding/status*", (route) => {
    requestedId = new URL(route.request().url()).searchParams.get("device_id") ?? "";
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ active_device_id: requestedId, active_agent_id: null, first_conversation_complete: false, next_action: "start_conversation" }) });
  });
  await page.goto("/console?device_id=device-second");
  await expect(page.getByText("开始第一次对话", { exact: true })).toBeVisible();
  expect(requestedId).toBe("device-second");
});


test("setup keeps manual progress separate from connection and claim completion", async ({ page }) => {
  await page.goto("/setup");
  await expect(page.getByText("插电，等设备显示配网提示。", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "下一步", exact: true }).click();
  await expect(page.getByText("在手机的 Wi-Fi 设置中连接 Xiaozhi-XXXX。", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "打开设备配网页" })).toBeHidden();
  await page.getByRole("button", { name: /返回并确认/ }).click();
  await expect(page.getByText(/配网成功还不代表云端在线/)).toBeVisible();
  await expect(page.getByRole("link", { name: "输入绑定码，认领设备" })).toHaveAttribute("href", "/claim");
  await expect(page.getByText("设备在线，可以聊天", { exact: true })).toHaveCount(0);
});
