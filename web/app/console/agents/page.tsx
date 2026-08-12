"use client";

import { FormEvent, useEffect, useState } from "react";

import { ErrorMessage, Loading } from "@/components/page-state";
import { api } from "@/lib/api";
import type { Agent, ModelPreset, VoicePreset } from "@/lib/types";

export default function AgentsPage() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [models, setModels] = useState<ModelPreset[]>([]);
  const [voices, setVoices] = useState<VoicePreset[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [form, setForm] = useState({ name: "", system_prompt: "", model_preset_id: "fast-chat", voice_preset_id: "cherry", memory_consent: false });
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  async function load(preferredId = selectedId) {
    const [agentList, modelList, voiceList] = await Promise.all([
      api<Agent[]>("/v1/agents"), api<ModelPreset[]>("/v1/model-presets"), api<VoicePreset[]>("/v1/voice-presets"),
    ]);
    setAgents(agentList); setModels(modelList); setVoices(voiceList);
    const selected = agentList.find((item) => item.id === preferredId) ?? agentList[0];
    if (selected) {
      setSelectedId(selected.id);
      setForm({ name: selected.name, system_prompt: selected.system_prompt, model_preset_id: selected.model_preset_id, voice_preset_id: selected.voice_preset_id, memory_consent: selected.memory_consent });
    }
    setReady(true);
  }

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      api<Agent[]>("/v1/agents"), api<ModelPreset[]>("/v1/model-presets"), api<VoicePreset[]>("/v1/voice-presets"),
    ]).then(([agentList, modelList, voiceList]) => {
      if (cancelled) return;
      setAgents(agentList); setModels(modelList); setVoices(voiceList);
      const selected = agentList[0];
      if (selected) {
        setSelectedId(selected.id);
        setForm({ name: selected.name, system_prompt: selected.system_prompt, model_preset_id: selected.model_preset_id, voice_preset_id: selected.voice_preset_id, memory_consent: selected.memory_consent });
      }
      setReady(true);
    }).catch((reason) => { if (!cancelled) setError(reason instanceof Error ? reason.message : "加载失败"); });
    return () => { cancelled = true; };
  }, []);

  function choose(id: string) {
    const selected = agents.find((item) => item.id === id);
    if (!selected) return;
    setSelectedId(id);
    setForm({ name: selected.name, system_prompt: selected.system_prompt, model_preset_id: selected.model_preset_id, voice_preset_id: selected.voice_preset_id, memory_consent: selected.memory_consent });
  }

  async function save(event: FormEvent) {
    event.preventDefault(); setError(null); setMessage(null);
    try {
      await api(`/v1/agents/${selectedId}`, { method: "PATCH", body: JSON.stringify(form) });
      setMessage("已保存，将从下一轮对话开始生效。"); await load(selectedId);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "保存失败"); }
  }

  async function createAgent() {
    setError(null);
    try {
      const created = await api<Agent>("/v1/agents", { method: "POST", body: JSON.stringify({ name: "新助手", system_prompt: "你是 Hensun Desk，一位自然、可靠的桌面 AI 助手。", model_preset_id: "fast-chat", voice_preset_id: "cherry" }) });
      setSelectedId(created.id); await load(created.id);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "创建失败"); }
  }

  if (!ready && !error) return <Loading />;
  return (
    <>
      <header className="page-head"><div><h2>智能体</h2><p>客户只看到产品预设，不会看到服务商地址或 API Key。</p></div><button className="button secondary" onClick={createAgent}>新建智能体</button></header>
      <ErrorMessage message={error} />{message ? <div className="success">{message}</div> : null}
      <div className="grid two" style={{ marginTop: 18 }}>
        <section className="card stack"><h2>选择智能体</h2>{agents.map((agent) => <button className={`button ${agent.id === selectedId ? "" : "secondary"}`} key={agent.id} onClick={() => choose(agent.id)}>{agent.name} · v{agent.config_version}</button>)}</section>
        <form className="card stack" onSubmit={save}>
          <div className="field"><label htmlFor="name">名称</label><input id="name" value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></div>
          <div className="field"><label htmlFor="model">对话预设</label><select id="model" value={form.model_preset_id} onChange={(event) => setForm({ ...form, model_preset_id: event.target.value })}>{models.map((model) => <option key={model.id} value={model.id}>{model.display_name} — {model.description}</option>)}</select></div>
          <div className="field"><label htmlFor="voice">音色</label><select id="voice" value={form.voice_preset_id} onChange={(event) => setForm({ ...form, voice_preset_id: event.target.value })}>{voices.map((voice) => <option key={voice.id} value={voice.id}>{voice.display_name}</option>)}</select></div>
          <div className="field"><label htmlFor="prompt">角色设定</label><textarea id="prompt" value={form.system_prompt} onChange={(event) => setForm({ ...form, system_prompt: event.target.value })} /></div>
          <label className="row"><input type="checkbox" checked={form.memory_consent} onChange={(event) => setForm({ ...form, memory_consent: event.target.checked })} />允许保存加密摘要记忆</label>
          <button className="button" type="submit" disabled={!selectedId}>保存配置</button>
        </form>
      </div>
    </>
  );
}
