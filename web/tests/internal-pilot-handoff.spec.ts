import { expect, test, type Page } from "@playwright/test";

const device = {
  id: "device-handoff",
  serial_number: "HENSUN-HANDOFF",
  board_type: "hensun-desk-v1",
  lifecycle: "owned",
  name: "Hensun Desk",
  hardware_version: "v1",
  firmware_version: "2.4.2",
  ota_auto_update: true,
  active_agent_id: "agent-default",
  active_profile_id: "profile-adult",
  online: true,
  last_seen_at: new Date().toISOString(),
};

const agent = {
  id: "agent-default",
  usage_profile_id: "profile-adult",
  name: "我的助手",
  avatar_url: null,
  system_prompt: "",
  model_preset_id: "balanced",
  voice_preset_id: "default",
  memory_consent: false,
  tools: {},
  llm_temperature: 0.7,
  tts_speech_rate: 1,
  config_version: 1,
  device_count: 1,
};

async function mockConsoleApi(page: Page) {
  await page.route("**/v1/onboarding/status", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      device_bound: true,
      assistant_configured: false,
      device_online: true,
      first_conversation_complete: false,
      next_action: "start_conversation",
      active_device_id: device.id,
      active_agent_id: agent.id,
    }),
  }));
  await page.route("**/v1/devices", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify([device]),
  }));
  await page.route("**/v1/agents", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify([agent]),
  }));
  await page.route("**/v1/account/usage", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      voice_turns: 0,
      provider_cost_micros: 0,
      pricing_configured: true,
      asr_units: 0,
      llm_input_units: 0,
      llm_output_units: 0,
      tts_units: 0,
    }),
  }));
}

test("first conversation uses Xiaocan wake word without a computer dependency", async ({ page }) => {
  await mockConsoleApi(page);

  await page.goto("/console");

  await expect(page.getByText("对设备说“你好小灿”")).toBeVisible();
  await expect(page.getByText(/电脑服务|本机控制面|同一 Wi-Fi/)).toHaveCount(0);
  await expect(page.getByText("设置助手")).toHaveCount(0);
});

test("owner must confirm before unbinding and sees the handoff state", async ({ page }) => {
  let unbindCalls = 0;
  let unbound = false;
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route("**/v1/devices", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(unbound ? [] : [device]),
  }));
  await page.route("**/v1/agents", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify([agent]),
  }));
  await page.route("**/v1/profiles", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify([{
      id: "profile-adult",
      kind: "adult",
      display_name: "本人",
      age_band: null,
      guardian_consent_version: null,
      guardian_consent_at: null,
      memory_consent: false,
      quiet_start: "22:00",
      quiet_end: "07:00",
      daily_limit_minutes: 120,
      continuous_reminder_minutes: 45,
    }]),
  }));
  await page.route("**/v1/devices/device-handoff/configuration", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      device_id: device.id,
      desired_version: 1,
      applied_version: 1,
      speaker_volume: 60,
      screen_brightness: 70,
      applied_speaker_volume: 60,
      applied_screen_brightness: 70,
      sync_status: "synced",
      last_error_code: null,
      command_id: null,
      updated_at: new Date().toISOString(),
      applied_at: new Date().toISOString(),
    }),
  }));
  await page.route("**/v1/devices/device-handoff/unbind", (route) => {
    unbindCalls += 1;
    unbound = true;
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ id: device.id, lifecycle: "factory-unclaimed", reset_epoch: 1 }),
    });
  });
  page.once("dialog", async (dialog) => {
    expect(dialog.type()).toBe("confirm");
    expect(dialog.message()).toContain("新用户");
    await dialog.accept();
  });

  await page.goto("/console/devices");
  const pageWidth = await page.evaluate(() => ({
    viewport: window.innerWidth,
    document: document.documentElement.scrollWidth,
  }));
  expect(pageWidth.document).toBeLessThanOrEqual(pageWidth.viewport);
  const unbindButton = page.getByRole("button", { name: "解除绑定" });
  const buttonBox = await unbindButton.boundingBox();
  expect(buttonBox).not.toBeNull();
  expect(Math.min(buttonBox!.width, buttonBox!.height)).toBeGreaterThanOrEqual(44);
  await unbindButton.click();

  await expect.poll(() => unbindCalls).toBe(1);
  await expect(page.getByText("设备已恢复为待新用户绑定")).toBeVisible();
  await expect(page.getByRole("heading", { name: "还没有设备" })).toBeVisible();
});
