"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";

import { ErrorMessage, InlineResult, Loading } from "@/components/page-state";
import { api } from "@/lib/api";
import type { Agent, ModelPreset, VoicePreset } from "@/lib/types";

const DEFAULT_PROMPT = "你是 Hensun Desk，一位自然、可靠的桌面 AI 助手。";
const personalities = [
  ["温和可靠", "你是 Hensun Desk，一位温和、可靠、善于倾听的桌面 AI 助手。回答自然简洁，不冒充真人。"],
  ["轻松有趣", "你是 Hensun Desk，一位轻松、有幽默感但不过度打趣的桌面 AI 助手。回答自然简洁，不冒充真人。"],
  ["高效直接", "你是 Hensun Desk，一位表达清楚、注重行动建议的桌面 AI 助手。优先给出简洁可执行的回答。"],
] as const;

export default function AgentsPage() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [models, setModels] = useState<ModelPreset[]>([]);
  const [voices, setVoices] = useState<VoicePreset[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [form, setForm] = useState({ name: "", system_prompt: DEFAULT_PROMPT, model_preset_id: "fast-chat", voice_preset_id: "cherry", memory_consent: false, tools: {} as Record<string, boolean> });
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const chooseFrom = useCallback((items: Agent[], id?: string) => {
    const selected = items.find((item) => item.id === id) ?? items[0];
    if (!selected) return;
    setSelectedId(selected.id);
    setForm({ name: selected.name, system_prompt: selected.system_prompt, model_preset_id: selected.model_preset_id, voice_preset_id: selected.voice_preset_id, memory_consent: selected.memory_consent, tools: selected.tools });
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
    try { await api(`/v1/agents/${selectedId}`, { method: "PATCH", body: JSON.stringify(form) }); setMessage("已保存。新的名称、声音和性格会从下一轮对话生效。"); await load(selectedId); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "保存失败"); }
    finally { setSubmitting(false); }
  }

  async function createAgent() {
    setError(null); setSubmitting(true);
    try { const created = await api<Agent>("/v1/agents", { method: "POST", body: JSON.stringify({ name: "新助手", system_prompt: DEFAULT_PROMPT, model_preset_id: "fast-chat", voice_preset_id: "cherry" }) }); await load(created.id); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "创建失败"); }
    finally { setSubmitting(false); }
  }

  if (!ready) return <Loading cards={2} />;
  return (
    <>
      <header className="page-head"><div><div className="eyebrow">Assistant</div><h1>助手</h1><p>先设置名字、性格和声音；模型细节放在高级设置里。</p></div><div className="page-actions"><button className="button secondary" type="button" disabled={submitting} onClick={() => void createAgent()}>新建助手</button></div></header>
      <ErrorMessage message={error} />{message ? <InlineResult>{message}</InlineResult> : null}
      <div className="grid two" style={{ marginTop: 18 }}>
        <section className="card stack"><h2>我的助手</h2>{agents.map((agent) => <button className={`button ${agent.id === selectedId ? "" : "secondary"}`} type="button" key={agent.id} onClick={() => chooseFrom(agents, agent.id)}>{agent.name}<span className="hint">v{agent.config_version}</span></button>)}</section>
        <form className="card stack" onSubmit={save}>
          <div className="field"><label htmlFor="name">名称</label><input id="name" maxLength={80} value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></div>
          <div className="field"><label htmlFor="personality">性格</label><select id="personality" value={personalities.find(([, prompt]) => prompt === form.system_prompt)?.[0] ?? "自定义"} onChange={(event) => { const choice = personalities.find(([name]) => name === event.target.value); if (choice) setForm({ ...form, system_prompt: choice[1] }); }}>{personalities.map(([name]) => <option key={name}>{name}</option>)}<option>自定义</option></select></div>
          <div className="field"><label htmlFor="voice">声音</label><select id="voice" value={form.voice_preset_id} onChange={(event) => setForm({ ...form, voice_preset_id: event.target.value })}>{voices.map((voice) => <option key={voice.id} value={voice.id}>{voice.display_name}</option>)}</select></div>
          {voices.find((voice) => voice.id === form.voice_preset_id)?.preview_url ? <audio controls preload="none" src={voices.find((voice) => voice.id === form.voice_preset_id)?.preview_url ?? undefined}>浏览器不支持音频试听</audio> : <div className="hint">当前音色尚未配置固定试听片段，保存后可直接在设备上试听。</div>}
          <label className="check"><input type="checkbox" checked={form.memory_consent} onChange={(event) => setForm({ ...form, memory_consent: event.target.checked })} />允许保存可查看、可删除的加密摘要记忆</label>
          <details className="details"><summary>高级设置</summary><div className="stack">
            <div className="field"><label htmlFor="model">对话风格</label><select id="model" value={form.model_preset_id} onChange={(event) => setForm({ ...form, model_preset_id: event.target.value })}>{models.map((model) => <option key={model.id} value={model.id}>{model.display_name} — {model.description}</option>)}</select></div>
            <div className="field"><label htmlFor="prompt">完整角色设定</label><textarea id="prompt" value={form.system_prompt} onChange={(event) => setForm({ ...form, system_prompt: event.target.value })} /></div>
            <button className="button secondary" type="button" onClick={() => setForm({ ...form, system_prompt: DEFAULT_PROMPT, model_preset_id: "fast-chat", voice_preset_id: "cherry", tools: {} })}>恢复默认设置</button>
          </div></details>
          <button className="button" type="submit" disabled={!selectedId || submitting}>{submitting ? "正在保存…" : "保存设置"}</button>
        </form>
      </div>
    </>
  );
}
