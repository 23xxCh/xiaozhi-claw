"use client";

import { useState } from "react";
import { api } from "@/lib/api";

type Library = {
  checked_at: string;
  voices: { id: string; name: string; voice: string; provider: string; language: string;
    models: { id: string; name: string; available: boolean }[] }[];
};

export function VoiceLibrary({ modelId, onChoose }: { modelId: string; onChoose: (id: string) => void }) {
  const [library, setLibrary] = useState<Library | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [scope, setScope] = useState("current");
  const [limit, setLimit] = useState(50);
  const providers: Record<string, string> = { dashscope: "阿里 Qwen TTS", "volc-tts": "火山 TTS 2.0", doubao: "豆包端到端", "aliyun-dialog": "阿里应用" };
  const matches = library?.voices.filter((v) =>
    (scope === "all" || (scope === "current" ? v.models.some((m) => m.id === modelId) : v.provider === scope))
    && `${v.name} ${v.voice} ${v.language}`.toLowerCase().includes(search.trim().toLowerCase())) ?? [];

  async function load() {
    setLoading(true); setError("");
    try { setLibrary(await api<Library>("/v1/voice-library")); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "目录加载失败"); }
    finally { setLoading(false); }
  }

  return <details className="details" onToggle={(event) => {
    if (event.currentTarget.open && !library && !loading && !error) void load();
  }}>
    <summary>完整音色目录（含未开放音色）</summary>
    <div className="stack">
      <p className="hint">目录收录当前 Qwen TTS、火山 TTS 2.0 和豆包全双工兼容音色。未开放音色仍需服务权限和实际发声验证；定制复刻音色不属于公共目录。不同方案可能使用同一个音色。</p>
      {loading ? <p role="status">正在加载目录…</p> : null}
      {error ? <div role="alert">{error} <button type="button" onClick={() => void load()}>重试</button></div> : null}
      {library ? <>
        <div className="field"><label htmlFor="library-scope">查看范围</label><select id="library-scope" value={scope} onChange={(e) => { setScope(e.target.value); setLimit(50); }}>
          <option value="current">当前方案兼容</option><option value="all">全部方案</option>
          {Object.entries(providers).map(([key, name]) => <option key={key} value={key}>{name}</option>)}
        </select></div>
        <div className="field"><label htmlFor="library-search">搜索完整目录</label><input id="library-search" type="search" placeholder="名称、音色编号或语言代码" value={search} onChange={(e) => { setSearch(e.target.value); setLimit(50); }} /></div>
        <p className="hint">匹配 {matches.length} 项；目录核对日期 {library.checked_at}。仅“当前方案可选”的音色可以应用。</p>
        <ul className="stack">{matches.slice(0, limit).map((v) => {
          const available = v.models.some((m) => m.id === modelId && m.available);
          return <li key={v.id}>
            <strong>{v.name}</strong> · {providers[v.provider] ?? v.provider} · {v.language}
            <div className="hint">{v.models.map((m) => m.name).join("、") || "暂无兼容方案"}</div>
            <button type="button" className="button secondary" disabled={!available} onClick={() => onChoose(v.id)}>{available ? "当前方案可选：使用此音色" : v.models.some((m) => m.available) ? "请切换到兼容方案" : "未开放，待验证"}</button>
          </li>;
        })}</ul>
        {matches.length === 0 ? <p>没有匹配音色，请更换搜索条件。</p> : null}
        {matches.length > limit ? <button type="button" className="button secondary" onClick={() => setLimit(limit + 50)}>再显示 50 项</button> : null}
      </> : null}
    </div>
  </details>;
}
