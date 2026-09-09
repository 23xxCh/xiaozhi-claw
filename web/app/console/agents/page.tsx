"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";

import { ErrorMessage, InlineResult, Loading } from "@/components/page-state";
import { VoiceLibrary } from "@/components/voice-library";
import { api } from "@/lib/api";
import type { Agent, ModelPreset, VoicePreset } from "@/lib/types";

const DEFAULT_PROMPT = "你是 Hensun Desk，一位自然、可靠的桌面 AI 助手。";
const personalities = [
  ["温和可靠", "你是 Hensun Desk，一位温和、可靠、善于倾听的桌面 AI 助手。回答自然简洁，不冒充真人。"],
  ["轻松有趣", "你是 Hensun Desk，一位轻松、有幽默感但不过度打趣的桌面 AI 助手。回答自然简洁，不冒充真人。"],
  ["高效直接", "你是 Hensun Desk，一位表达清楚、注重行动建议的桌面 AI 助手。优先给出简洁可执行的回答。"],
] as const;

const availableTools = [
  ["current_time", "当前时间", "回答现在几点"],
  ["calculator", "计算器", "计算简单四则运算"],
  ["weather", "天气", "查询城市天气，需服务端已开通联网搜索"],
  ["web_search", "联网搜索", "查询最新公开信息，需服务端已开通"],
  ["self.audio_speaker.set_volume", "设备音量", "允许助手调整扬声器音量"],
  ["self.screen.set_brightness", "屏幕亮度", "允许助手调整屏幕亮度"],
] as const;

export default function AgentsPage() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [models, setModels] = useState<ModelPreset[]>([]);
  const [voices, setVoices] = useState<VoicePreset[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [form, setForm] = useState({ name: "", system_prompt: DEFAULT_PROMPT, model_preset_id: "", voice_preset_id: "", memory_consent: false, tools: {} as Record<string, boolean>, llm_temperature: 0.6, tts_speech_rate: 1.0 });
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [voiceSearch, setVoiceSearch] = useState("");
  const selectedModel = models.find((model) => model.id === form.model_preset_id);
  const compatibleVoices = voices.filter((voice) => selectedModel?.compatible_voice_ids.includes(voice.id));
  const selectedVoice = compatibleVoices.find((voice) => voice.id === form.voice_preset_id);
  const visibleVoices = compatibleVoices.filter((voice) => voice.display_name.toLowerCase().includes(voiceSearch.trim().toLowerCase()));

  function chooseModel(id: string) {
    const model = models.find((item) => item.id === id);
    if (!model) return;
    setVoiceSearch("");
    setForm({ ...form, model_preset_id: id, voice_preset_id: model.compatible_voice_ids.includes(form.voice_preset_id) ? form.voice_preset_id : model.default_voice_preset_id ?? "" });
    setMessage("已切换语音方案，请确认声音后保存。正在进行的对话会继续完成。");
  }

  const chooseFrom = useCallback((items: Agent[], id?: string) => {
    const selected = items.find((item) => item.id === id) ?? items[0];
    if (!selected) return;
    setSelectedId(selected.id);
    setForm({ name: selected.name, system_prompt: selected.system_prompt, model_preset_id: selected.model_preset_id, voice_preset_id: selected.voice_preset_id, memory_consent: selected.memory_consent, tools: selected.tools, llm_temperature: selected.llm_temperature, tts_speech_rate: selected.tts_speech_rate });
  }, []);

  const load = useCallback(async (preferredId?: string) => {
    const [agentList, modelList, voiceList] = await Promise.all([api<Agent[]>("/v1/agents"), api<ModelPreset[]>("/v1/model-presets"), api<VoicePreset[]>("/v1/voice-presets")]);
    setAgents(agentList); setModels(modelList); setVoices(voiceList); chooseFrom(agentList, preferredId); setReady(true); setError(null);
  }, [chooseFrom]);

  useEffect(() => {
    const timer = window.setTimeout(() => void load().catch((reason) => { setReady(true); setError(reason instanceof Error ? reason.message : "助手设置加载失败"); }), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  async function save(event: FormEvent) {
    event.preventDefault(); setError(null); setMessage(null); setSubmitting(true);
    const payload = {
      name: form.name,
      ...(selectedModel?.capabilities.system_prompt !== false ? { system_prompt: form.system_prompt } : {}),
      model_preset_id: form.model_preset_id, voice_preset_id: form.voice_preset_id,
      memory_consent: form.memory_consent,
      ...(selectedModel?.capabilities.tools ? { tools: form.tools } : {}),
      ...(selectedModel?.capabilities.llm_temperature ? { llm_temperature: form.llm_temperature } : {}),
      ...(selectedModel?.capabilities.tts_speech_rate ? { tts_speech_rate: form.tts_speech_rate } : {}),
    };
    try { await api(`/v1/agents/${selectedId}`, { method: "PATCH", body: JSON.stringify(payload) }); setMessage("已保存。新设置从下一轮对话生效；具体支持的设置以所选方案说明为准。"); await load(selectedId); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "保存失败"); }
    finally { setSubmitting(false); }
  }

  async function createAgent() {
    setError(null); setSubmitting(true);
    try { const created = await api<Agent>("/v1/agents", { method: "POST", body: JSON.stringify({ name: "新助手" }) }); await load(created.id); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "创建失败"); }
    finally { setSubmitting(false); }
  }

  if (!ready) return <Loading cards={2} />;
  return (
    <>
      <header className="page-head"><div><div className="eyebrow">Assistant</div><h1>助手</h1><p>每个助手可选择自己的语音方案、性格和声音。</p></div><div className="page-actions"><button className="button secondary" type="button" disabled={submitting} onClick={() => void createAgent()}>新建助手</button></div></header>
      <ErrorMessage message={error} />{message ? <InlineResult>{message}</InlineResult> : null}
      <div className="grid two" style={{ marginTop: 18 }}>
        <section className="card stack"><h2>我的助手</h2>{agents.map((agent) => <button className={`button ${agent.id === selectedId ? "" : "secondary"}`} type="button" key={agent.id} onClick={() => chooseFrom(agents, agent.id)}>{agent.name}<span className="hint">v{agent.config_version}</span></button>)}</section>
        <form className="card stack" onSubmit={save}>
          <div className="field"><label htmlFor="name">名称</label><input id="name" maxLength={80} value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></div>
          <div className="field"><label htmlFor="personality">性格</label><select disabled={selectedModel?.capabilities.system_prompt === false} id="personality" value={personalities.find(([, prompt]) => prompt === form.system_prompt)?.[0] ?? "自定义"} onChange={(event) => { const choice = personalities.find(([name]) => name === event.target.value); if (choice) setForm({ ...form, system_prompt: choice[1] }); }}>{personalities.map(([name]) => <option key={name}>{name}</option>)}<option>自定义</option></select></div>
          <div className="field"><label htmlFor="model">语音方案</label><select id="model" value={form.model_preset_id} onChange={(event) => chooseModel(event.target.value)}>{!selectedModel ? <option value={form.model_preset_id}>请选择可用方案</option> : null}{models.map((model) => <option key={model.id} value={model.id}>{model.display_name}</option>)}</select><div className="hint">{selectedModel?.description ?? "当前方案不可用，请重新选择。"}</div></div>
          <p className="hint">{selectedModel?.capabilities.history === false ? "当前方案每轮独立，使用阿里应用的人设与云端能力，不发送 Hensun 的历史和摘要记忆，也不执行设备工具。原角色设置保留，切回后恢复。" : "切换后，当前会话最近最多 10 组问答会随下一轮发送给所选语音服务。"}</p>
          <div className="field"><label htmlFor="voice-search">查找声音（当前方案可选 {compatibleVoices.length} 种）</label><input id="voice-search" type="search" placeholder="搜索名称或风格，如温柔、男声" value={voiceSearch} onChange={(event) => setVoiceSearch(event.target.value)} /></div>
          <div className="field"><label htmlFor="voice">声音</label><select id="voice" value={form.voice_preset_id} onChange={(event) => setForm({ ...form, voice_preset_id: event.target.value })}>{selectedVoice && !visibleVoices.includes(selectedVoice) ? <option value={selectedVoice.id}>{selectedVoice.display_name}（当前选择）</option> : null}{visibleVoices.map((voice) => <option key={voice.id} value={voice.id}>{voice.display_name}</option>)}</select>{visibleVoices.length === 0 ? <div className="hint">没有匹配的声音，请更换关键词。</div> : null}</div>
          <p className="hint">试听展示基础音色；实际语气由语音方案决定。阿里多模态应用的具体音色在阿里控制台配置。</p>
          <VoiceLibrary modelId={form.model_preset_id} onChoose={(id) => { setVoiceSearch(""); setForm({ ...form, voice_preset_id: id }); setMessage("已选择音色，请保存设置。"); }} />
          {selectedVoice?.preview_url ? <audio controls preload="none" src={selectedVoice.preview_url}>浏览器不支持音频试听</audio> : <div className="hint">当前音色尚未配置固定试听片段，保存后可直接在设备上试听。</div>}
          <label className="check"><input type="checkbox" checked={form.memory_consent} onChange={(event) => setForm({ ...form, memory_consent: event.target.checked })} />允许保存可查看、可删除的加密摘要记忆</label>
          <p className="hint">启用记忆后，已完成对话会交给摘要服务生成记忆；摘要服务可能与所选语音服务不同。</p>
          <details className="details"><summary>高级设置</summary><div className="stack">
            {selectedModel?.capabilities.llm_temperature ? <div className="field"><label htmlFor="temperature">表达灵活度：{form.llm_temperature.toFixed(2)}</label><input id="temperature" type="range" min="0" max="2" step="0.05" value={form.llm_temperature} onChange={(event) => setForm({ ...form, llm_temperature: Number(event.target.value) })} /><div className="hint">低值更稳定，高值更多变化；建议保持 0.3–0.8。</div></div> : <div className="hint">当前方案的表达灵活度固定；原方案设置已保留。</div>}
            {selectedModel?.capabilities.tts_speech_rate ? <div className="field"><label htmlFor="speech-rate">说话速度：{form.tts_speech_rate.toFixed(2)}×</label><input id="speech-rate" type="range" min="0.5" max="2" step="0.05" value={form.tts_speech_rate} onChange={(event) => setForm({ ...form, tts_speech_rate: Number(event.target.value) })} /></div> : <div className="hint">当前方案的说话速度固定；原方案设置已保留。</div>}
            {selectedModel?.capabilities.tools ? <div className="field"><span className="label">工具权限</span><div className="stack">{availableTools.filter(([id]) => selectedModel.capabilities.supported_tool_ids.includes(id)).map(([id, label, hint]) => <label className="check" key={id}><input type="checkbox" checked={Boolean(form.tools[id])} onChange={(event) => setForm({ ...form, tools: { ...form.tools, [id]: event.target.checked } })} />{label}<span className="hint">{hint}</span></label>)}</div><div className="hint">工具只在本助手启用后提供给模型；高风险设备操作未开放。</div></div> : <div className="hint">当前方案暂不支持工具；原有工具权限已保留。</div>}
            <div className="field"><label htmlFor="prompt">完整角色设定</label><textarea disabled={selectedModel?.capabilities.system_prompt === false} id="prompt" value={form.system_prompt} onChange={(event) => setForm({ ...form, system_prompt: event.target.value })} /></div>
            <button className="button secondary" type="button" onClick={() => { const model = models.find((item) => item.is_default) ?? models[0]; if (model) setForm({ ...form, system_prompt: DEFAULT_PROMPT, model_preset_id: model.id, voice_preset_id: model.default_voice_preset_id ?? "", tools: {}, llm_temperature: 0.6, tts_speech_rate: 1.0 }); }}>恢复默认设置</button>
          </div></details>
          <button className="button" type="submit" disabled={!selectedId || !selectedModel || !selectedVoice || submitting}>{submitting ? "正在保存…" : "保存设置"}</button>
        </form>
      </div>
    </>
  );
}
