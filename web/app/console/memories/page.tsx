"use client";

import { FormEvent, useEffect, useState } from "react";

import { ErrorMessage, InlineResult, Loading } from "@/components/page-state";
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
  const [message, setMessage] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function loadMemories(id: string) {
    const [items, sessions] = await Promise.all([api<Memory[]>(`/v1/agents/${id}/memories`), api<Conversation[]>("/v1/conversations")]);
    setMemories(items); setConversations(sessions.filter((item) => item.agent_id === id && item.summary));
  }
  useEffect(() => { api<Agent[]>("/v1/agents").then(async (items) => { setAgents(items); const id = items[0]?.id ?? ""; setAgentId(id); if (id && items[0].memory_consent) await loadMemories(id); setReady(true); }).catch((reason) => { setReady(true); setError(reason instanceof Error ? reason.message : "记忆加载失败"); }); }, []);

  async function choose(id: string) { setAgentId(id); setError(null); const agent = agents.find((item) => item.id === id); if (agent?.memory_consent) await loadMemories(id); else { setMemories([]); setConversations([]); } }
  async function save(event: FormEvent) { event.preventDefault(); setError(null); setMessage(null); setSubmitting(true); try { await api(`/v1/agents/${agentId}/memories/${keyName}`, { method: "PUT", body: JSON.stringify({ key: keyName, value }) }); setValue(""); setMessage("摘要已经加密保存。你可以随时修改或删除。\n"); await loadMemories(agentId); } catch (reason) { setError(reason instanceof Error ? reason.message : "保存失败"); } finally { setSubmitting(false); } }
  async function remove(key: string) { if (!window.confirm("确认删除这条摘要记忆？删除后不可恢复。")) return; await api(`/v1/agents/${agentId}/memories/${key}`, { method: "DELETE" }); await loadMemories(agentId); }
  async function removeSummary(id: string) { if (!window.confirm("确认删除这条会话摘要？")) return; await api(`/v1/conversations/${id}/summary`, { method: "DELETE" }); await loadMemories(agentId); }
  async function exportAll() { const payload = await api<Record<string, unknown>>("/v1/memories/export"); const url = URL.createObjectURL(new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" })); const link = document.createElement("a"); link.href = url; link.download = "hensun-memory-export.json"; link.click(); URL.revokeObjectURL(url); }
  async function deleteAll() { if (!window.confirm("确认删除账户下全部摘要记忆？此操作不可恢复。")) return; await api("/v1/memories", { method: "DELETE" }); setMemories([]); setConversations([]); }

  const selected = agents.find((item) => item.id === agentId);
  if (!ready) return <Loading cards={2} />;
  return (
    <>
      <header className="page-head"><div><div className="eyebrow">Memory</div><h1>记忆</h1><p>这里只有你主动授权的摘要，不会出现原始音频或逐句对话。</p></div><div className="page-actions"><button className="button secondary" type="button" onClick={() => void exportAll()}>导出</button><button className="button danger" type="button" onClick={() => void deleteAll()}>删除全部</button></div></header>
      <ErrorMessage message={error} />{message ? <InlineResult>{message}</InlineResult> : null}
      <div className="grid two" style={{ marginTop: 18 }}>
        <section className="card stack">
          <div className="field"><label htmlFor="agent-memory">助手</label><select id="agent-memory" value={agentId} onChange={(event) => void choose(event.target.value)}>{agents.map((agent) => <option key={agent.id} value={agent.id}>{agent.name}</option>)}</select></div>
          {!selected?.memory_consent ? <InlineResult tone="warning">这个助手没有开启记忆。可以在“助手”页面主动开启。</InlineResult> : null}
          {selected?.memory_consent && memories.length === 0 && conversations.length === 0 ? <div className="card soft"><strong>还没有记忆</strong><p className="muted">未来保存的偏好、提醒和摘要会出现在这里。</p></div> : null}
          {memories.map((memory) => <div className="card soft" key={memory.id}><strong>{memory.key}</strong><p>{memory.value}</p><button className="button danger" type="button" onClick={() => void remove(memory.key)}>删除</button></div>)}
          {conversations.map((conversation) => <div className="card soft" key={conversation.id}><strong>会话摘要 · {new Date(conversation.started_at).toLocaleString()}</strong><p>{conversation.summary}</p><button className="button danger" type="button" onClick={() => void removeSummary(conversation.id)}>删除摘要</button></div>)}
        </section>
        <form className="card stack" onSubmit={save}><h2>添加或修改</h2><div className="field"><label htmlFor="memory-key">分类</label><input id="memory-key" pattern="[a-z0-9._-]+" value={keyName} onChange={(event) => setKeyName(event.target.value)} /></div><div className="field"><label htmlFor="memory-value">摘要内容</label><textarea id="memory-value" value={value} onChange={(event) => setValue(event.target.value)} /></div><button className="button" type="submit" disabled={submitting || !selected?.memory_consent || !value}>{submitting ? "正在保存…" : "保存加密摘要"}</button></form>
      </div>
    </>
  );
}
