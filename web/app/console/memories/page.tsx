"use client";

import { FormEvent, useEffect, useState } from "react";

import { ErrorMessage, Loading } from "@/components/page-state";
import { api } from "@/lib/api";
import type { Agent, Memory } from "@/lib/types";

type Conversation = { id: string; agent_id: string; started_at: string; summary: string | null };

export default function MemoriesPage() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [agentId, setAgentId] = useState("");
  const [memories, setMemories] = useState<Memory[]>([]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [keyName, setKeyName] = useState("preference");
  const [value, setValue] = useState("");
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function loadMemories(id: string) {
    const [items, sessions] = await Promise.all([
      api<Memory[]>(`/v1/agents/${id}/memories`),
      api<Conversation[]>("/v1/conversations"),
    ]);
    setMemories(items); setConversations(sessions.filter((item) => item.agent_id === id && item.summary));
  }
  useEffect(() => {
    api<Agent[]>("/v1/agents").then(async (items) => {
      setAgents(items); const id = items[0]?.id ?? ""; setAgentId(id);
      if (id && items[0].memory_consent) await loadMemories(id);
      setReady(true);
    }).catch((reason) => setError(reason instanceof Error ? reason.message : "加载失败"));
  }, []);

  async function choose(id: string) {
    setAgentId(id); setError(null);
    const agent = agents.find((item) => item.id === id);
    if (agent?.memory_consent) await loadMemories(id); else { setMemories([]); setConversations([]); }
  }
  async function save(event: FormEvent) {
    event.preventDefault(); setError(null);
    try { await api(`/v1/agents/${agentId}/memories/${keyName}`, { method: "PUT", body: JSON.stringify({ key: keyName, value }) }); setValue(""); await loadMemories(agentId); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "保存失败"); }
  }
  async function remove(key: string) {
    if (!window.confirm("确认删除这条摘要记忆？删除后不可恢复。")) return;
    await api(`/v1/agents/${agentId}/memories/${key}`, { method: "DELETE" }); await loadMemories(agentId);
  }

  async function removeSummary(id: string) {
    if (!window.confirm("确认删除这条会话摘要？")) return;
    await api(`/v1/conversations/${id}/summary`, { method: "DELETE" }); await loadMemories(agentId);
  }

  async function exportAll() {
    const payload = await api<Record<string, unknown>>("/v1/memories/export");
    const url = URL.createObjectURL(new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" }));
    const link = document.createElement("a"); link.href = url; link.download = "hensun-memory-export.json"; link.click(); URL.revokeObjectURL(url);
  }

  async function deleteAll() {
    if (!window.confirm("确认删除账户下全部摘要记忆？此操作不可恢复。")) return;
    await api("/v1/memories", { method: "DELETE" }); setMemories([]); setConversations([]);
  }

  const selected = agents.find((item) => item.id === agentId);
  if (!ready && !error) return <Loading />;
  return (
    <>
      <header className="page-head"><div><h2>摘要记忆</h2><p>这里不会出现原始音频或逐句对话，只显示你授权保存的加密摘要。</p></div><div className="row"><button className="button secondary" onClick={exportAll}>导出全部</button><button className="button danger" onClick={deleteAll}>删除全部</button></div></header>
      <ErrorMessage message={error} />
      <div className="grid two">
        <section className="card stack">
          <div className="field"><label htmlFor="agent-memory">智能体</label><select id="agent-memory" value={agentId} onChange={(event) => choose(event.target.value)}>{agents.map((agent) => <option key={agent.id} value={agent.id}>{agent.name}</option>)}</select></div>
          {!selected?.memory_consent ? <div className="muted">该智能体尚未开启摘要记忆。请先到“智能体”页面主动开启。</div> : (
            memories.length ? memories.map((memory) => <div className="card" key={memory.id}><strong>{memory.key}</strong><p>{memory.value}</p><button className="button danger" onClick={() => remove(memory.key)}>删除</button></div>) : <div className="muted">暂无手动摘要</div>
          )}
          {conversations.map((conversation) => <div className="card" key={conversation.id}><strong>会话摘要 · {new Date(conversation.started_at).toLocaleString()}</strong><p>{conversation.summary}</p><button className="button danger" onClick={() => removeSummary(conversation.id)}>删除摘要</button></div>)}
        </section>
        <form className="card stack" onSubmit={save}>
          <h2>添加或修改</h2>
          <div className="field"><label htmlFor="memory-key">键名</label><input id="memory-key" pattern="[a-z0-9._-]+" value={keyName} onChange={(event) => setKeyName(event.target.value)} /></div>
          <div className="field"><label htmlFor="memory-value">内容</label><textarea id="memory-value" value={value} onChange={(event) => setValue(event.target.value)} /></div>
          <button className="button" type="submit" disabled={!selected?.memory_consent || !value}>保存加密摘要</button>
        </form>
      </div>
    </>
  );
}
