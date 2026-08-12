"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { ErrorMessage, Loading } from "@/components/page-state";
import { api } from "@/lib/api";
import type { Agent, Device, Usage } from "@/lib/types";

export default function DashboardPage() {
  const [data, setData] = useState<{ agents: Agent[]; devices: Device[]; usage: Usage } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      api<Agent[]>("/v1/agents"),
      api<Device[]>("/v1/devices"),
      api<Usage>("/v1/account/usage"),
    ])
      .then(([agents, devices, usage]) => setData({ agents, devices, usage }))
      .catch((reason) => setError(reason instanceof Error ? reason.message : "加载失败"));
  }, []);

  if (!data && !error) return <Loading />;
  return (
    <>
      <header className="page-head">
        <div><h2>控制台概览</h2><p>设备、智能体和成本数据均来自自有控制面。</p></div>
        <Link className="button" href="/console/devices">绑定设备</Link>
      </header>
      <ErrorMessage message={error} />
      {data ? (
        <div className="grid">
          <section className="card"><div className="muted">设备</div><div className="metric">{data.devices.length}</div><div>{data.devices.filter((item) => item.online).length} 台在线</div></section>
          <section className="card"><div className="muted">智能体</div><div className="metric">{data.agents.length}</div><div>配置在下一轮对话生效</div></section>
          <section className="card"><div className="muted">本月语音轮数</div><div className="metric">{data.usage.voice_turns}</div><div>{data.usage.pricing_configured ? `估算服务成本 ¥${(data.usage.provider_cost_micros / 1_000_000).toFixed(2)}` : "成本费率待管理员配置"}</div></section>
          <section className="card" style={{ gridColumn: "1 / -1" }}>
            <h2>隐私默认值</h2>
            <p className="muted">不保存原始音频和逐句对话。只有你主动开启智能体记忆后，才会保存加密摘要，并可随时修改或删除。</p>
          </section>
        </div>
      ) : null}
    </>
  );
}
