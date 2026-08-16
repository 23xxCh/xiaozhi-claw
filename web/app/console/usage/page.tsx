"use client";

import { useCallback, useEffect, useState } from "react";

import { Loading, SectionError } from "@/components/page-state";
import { api } from "@/lib/api";
import type { Usage } from "@/lib/types";

export default function UsagePage() {
  const [usage, setUsage] = useState<Usage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const load = useCallback(async () => { try { setUsage(await api<Usage>("/v1/account/usage")); setUpdatedAt(new Date()); setError(null); } catch (reason) { setError(reason instanceof Error ? reason.message : "用量加载失败"); } }, []);
  useEffect(() => { const timer = window.setTimeout(() => void load(), 0); return () => window.clearTimeout(timer); }, [load]);
  if (!usage && !error) return <Loading />;
  if (!usage) return <SectionError message={error ?? "加载失败"} onRetry={() => void load()} />;
  return (
    <><header className="page-head"><div><div className="eyebrow">Usage</div><h1>用量</h1><p>当前免费内测不收费；这里帮助我们一起观察真实使用和服务稳定性。</p></div></header>{error ? <SectionError message={error} onRetry={() => void load()} updatedAt={updatedAt} /> : null}<div className="grid"><section className="card"><div className="muted">语音轮数</div><div className="metric">{usage.voice_turns}</div></section><section className="card"><div className="muted">听你说话</div><div className="metric">{Math.round(usage.asr_units / 1000)}s</div></section><section className="card"><div className="muted">助手回复文字</div><div className="metric">{usage.tts_units}</div><div>字符</div></section></div><section className="card soft" style={{ marginTop: 18 }}><h2>隐私与费用</h2><p className="muted">我们只记录轮数、耗时和模型资源用量，不保存原始音频或逐句对话。内测期间服务商成本由 Hensun 承担。</p></section>{updatedAt ? <p className="hint">最后更新 {updatedAt.toLocaleTimeString()}</p> : null}</>
  );
}
