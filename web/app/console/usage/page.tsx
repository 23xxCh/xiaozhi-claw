"use client";

import { useEffect, useState } from "react";

import { ErrorMessage, Loading } from "@/components/page-state";
import { api } from "@/lib/api";
import type { Usage } from "@/lib/types";

export default function UsagePage() {
  const [usage, setUsage] = useState<Usage | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { api<Usage>("/v1/account/usage").then(setUsage).catch((reason) => setError(reason instanceof Error ? reason.message : "加载失败")); }, []);
  if (!usage && !error) return <Loading />;
  return (
    <><header className="page-head"><div><h2>用量与成本</h2><p>首版免费；费率配置后按账户估算模型消耗。</p></div></header><ErrorMessage message={error} />{usage ? <div className="grid"><section className="card"><div className="muted">语音轮数</div><div className="metric">{usage.voice_turns}</div></section><section className="card"><div className="muted">ASR 音频时长</div><div className="metric">{(usage.asr_units / 1000).toFixed(1)}s</div></section><section className="card"><div className="muted">LLM 输出 Token（估算）</div><div className="metric">{usage.llm_output_units}</div></section><section className="card"><div className="muted">TTS 输入字符</div><div className="metric">{usage.tts_units}</div></section><section className="card"><div className="muted">服务商成本</div><div className="metric">{usage.pricing_configured ? `¥${(usage.provider_cost_micros / 1_000_000).toFixed(2)}` : "待配置"}</div></section></div> : null}</>
  );
}
