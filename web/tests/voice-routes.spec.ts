import { expect, test, type Page } from "@playwright/test";

import type { Agent, ModelPreset, VoicePreset } from "../lib/types";

const toolIds = ["current_time", "calculator", "weather", "web_search", "self.audio_speaker.set_volume", "self.screen.set_brightness"];
const models: ModelPreset[] = [
  { id: "fast-chat", display_name: "快速对话", description: "经典语音方案", is_default: false, route_kind: "cascade", capabilities: { llm_temperature: true, tts_speech_rate: true, tools: true, supported_tool_ids: toolIds }, compatible_voice_ids: ["cherry", "ethan"], default_voice_preset_id: "cherry" },
  { id: "doubao-realtime", display_name: "豆包实时语音", description: "表达灵活度和语速固定", is_default: true, route_kind: "realtime_s2s", capabilities: { llm_temperature: false, tts_speech_rate: false, tools: true, supported_tool_ids: toolIds }, compatible_voice_ids: ["doubao-vv"], default_voice_preset_id: "doubao-vv" },
];
const voices: VoicePreset[] = [
  { id: "cherry", display_name: "Cherry", language: "zh-CN", provider: "dashscope", voice: "Cherry", preview_url: null, is_default: true },
  { id: "ethan", display_name: "Ethan", language: "zh-CN", provider: "dashscope", voice: "Ethan", preview_url: null, is_default: false },
  { id: "doubao-vv", display_name: "VV", language: "zh-CN", provider: "doubao", voice: "zh_female_vv_jupiter_bigtts", preview_url: null, is_default: false },
];

async function mockCatalog(page: Page) {
  const agents: Agent[] = [{ id: "role-1", usage_profile_id: "adult-1", name: "原助手", avatar_url: null, system_prompt: "原角色设定", model_preset_id: "fast-chat", voice_preset_id: "cherry", memory_consent: false, tools: { calculator: true }, llm_temperature: 0.85, tts_speech_rate: 1.25, config_version: 3, device_count: 1 }];
  const patches: Record<string, unknown>[] = [];
  const creates: Record<string, unknown>[] = [];
  await page.route("**/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    let body: unknown = [];
    if (path === "/v1/model-presets") body = models;
    if (path === "/v1/voice-presets") body = voices;
    if (path === "/v1/agents") {
      if (request.method() === "POST") {
        const payload = request.postDataJSON();
        creates.push(payload);
        const created = { ...agents[0], ...payload, id: "role-2", model_preset_id: "doubao-realtime", voice_preset_id: "doubao-vv", config_version: 1 };
        agents.push(created);
        body = created;
      } else body = agents;
    }
    if (path.startsWith("/v1/agents/") && request.method() === "PATCH") {
      const payload = request.postDataJSON();
      patches.push(payload);
      agents[0] = { ...agents[0], ...payload, config_version: agents[0].config_version + 1 };
      body = agents[0];
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
  await page.goto("/console/agents");
  await expect(page.getByLabel("名称", { exact: true })).toHaveValue("原助手");
  return { patches, creates };
}

test("switch filters voices and omits fixed parameters while preserving cascade settings", async ({ page }) => {
  const { patches } = await mockCatalog(page);
  await page.getByText("高级设置", { exact: true }).click();
  await expect(page.getByRole("slider", { name: /表达灵活度/ })).toHaveValue("0.85");
  await page.getByLabel("语音方案", { exact: true }).selectOption("doubao-realtime");
  await expect(page.getByLabel("声音", { exact: true }).locator("option")).toHaveText(["VV"]);
  await expect(page.getByRole("slider", { name: /表达灵活度|说话速度/ })).toHaveCount(0);
  await expect(page.getByText("当前方案的表达灵活度固定；原方案设置已保留。")).toBeVisible();
  await expect(page.getByRole("checkbox", { name: /计算器/ })).toBeChecked();
  await page.getByRole("checkbox", { name: /当前时间/ }).check();
  await page.getByRole("button", { name: "保存设置", exact: true }).click();
  await expect.poll(() => patches.length).toBe(1);
  expect(patches[0]).toMatchObject({ model_preset_id: "doubao-realtime", voice_preset_id: "doubao-vv", tools: { current_time: true, calculator: true } });
  expect(patches[0]).not.toHaveProperty("llm_temperature");
  expect(patches[0]).not.toHaveProperty("tts_speech_rate");
  await expect(page.getByText(/已保存。新的语音方案/)).toBeVisible();
  await page.getByLabel("语音方案", { exact: true }).selectOption("fast-chat");
  await expect(page.getByLabel("声音", { exact: true }).locator("option")).toHaveText(["Cherry", "Ethan"]);
  await expect(page.getByRole("slider", { name: /表达灵活度/ })).toHaveValue("0.85");
  await expect(page.getByRole("slider", { name: /说话速度/ })).toHaveValue("1.25");
});

test("new assistant leaves route and voice to the current server default", async ({ page }) => {
  const { creates } = await mockCatalog(page);
  await page.getByRole("button", { name: "新建助手", exact: true }).click();
  await expect.poll(() => creates.length).toBe(1);
  expect(creates[0]).not.toHaveProperty("model_preset_id");
  expect(creates[0]).not.toHaveProperty("voice_preset_id");
  await expect(page.getByLabel("语音方案", { exact: true })).toHaveValue("doubao-realtime");
  await expect(page.getByLabel("声音", { exact: true })).toHaveValue("doubao-vv");
});
