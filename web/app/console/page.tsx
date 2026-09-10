"use client";

import { ArrowRightIcon } from "@phosphor-icons/react/dist/csr/ArrowRight";
import { CheckIcon } from "@phosphor-icons/react/dist/csr/Check";
import { MicrophoneIcon } from "@phosphor-icons/react/dist/csr/Microphone";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { BrandFace } from "@/components/brand-face";
import { Loading, SectionError } from "@/components/page-state";
import { api } from "@/lib/api";
import { CLAIM_DEVICE_KEY } from "@/lib/onboarding";
import type { Agent, Device, OnboardingStatus, Usage } from "@/lib/types";

type Dashboard = { onboarding: OnboardingStatus; agents: Agent[]; devices: Device[]; usage: Usage };

const journey = [
  ["bind_device", "绑定设备", "输入设备屏幕上的 6 位代码"],
  ["bring_device_online", "确认设备在线", "设备会自动连接云端服务，离线时请重启设备"],
  ["start_conversation", "开始第一次对话", "对设备说“你好小灿”"],
] as const;

export default function DashboardPage() {
  const [data, setData] = useState<Dashboard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [paused, setPaused] = useState(false);

  const load = useCallback(async (isCurrent: () => boolean = () => true) => {
    try {
      const [agents, devices, usage] = await Promise.all([
        api<Agent[]>("/v1/agents"),
        api<Device[]>("/v1/devices"),
        api<Usage>("/v1/account/usage"),
      ]);
      if (!isCurrent()) return;
      const explicitId = new URLSearchParams(window.location.search).get("device_id");
      const rememberedId = window.sessionStorage.getItem(CLAIM_DEVICE_KEY);
      const selectedId = explicitId || (devices.some((device) => device.id === rememberedId) ? rememberedId : null);
      const onboarding = await api<OnboardingStatus>(selectedId ? `/v1/onboarding/status?device_id=${encodeURIComponent(selectedId)}` : "/v1/onboarding/status");
      if (!isCurrent()) return;
      setData({ onboarding, agents, devices, usage });
      setUpdatedAt(new Date());
      setError(null);
    } catch (reason) {
      if (!isCurrent()) return;
      setError(reason instanceof Error ? reason.message : "控制台暂时无法更新");
    }
  }, []);

  useEffect(() => {
    let active = true;
    let checks = 0;
    let timer: number;
    async function poll() {
      await load(() => active);
      if (!active) return;
      checks += 1;
      if (checks < 30) timer = window.setTimeout(() => void poll(), 5000);
      else setPaused(true);
    }
    timer = window.setTimeout(() => { setPaused(false); void poll(); }, 0);
    return () => { active = false; window.clearTimeout(timer); };
  }, [load, refresh]);
  if (!data && !error) return <Loading />;
  if (!data) return <SectionError message={error ?? "加载失败"} onRetry={() => setRefresh((value) => value + 1)} />;

  const currentDevice = data.devices.find((item) => item.id === data.onboarding.active_device_id) ?? data.devices[0];
  const currentAgent = data.agents.find((item) => item.id === data.onboarding.active_agent_id) ?? data.agents[0];
  const currentIndex = journey.findIndex(([key]) => key === data.onboarding.next_action);

  return (
    <>
      <header className="page-head">
        <div><div className="eyebrow">Good day</div><h1>你好，今天想聊点什么？</h1><p>先确认设备状态，再开始一段自然的对话。</p></div><button className="button secondary" type="button" onClick={() => setRefresh((value) => value + 1)}>刷新使用进度</button>
      </header>
      {error ? <SectionError message={error} onRetry={() => setRefresh((value) => value + 1)} updatedAt={updatedAt} /> : null}

      {data.onboarding.next_action !== "complete" ? (
        <section className="card hero-panel" style={{ marginBottom: 18 }}>
          <div className="split">
            <div className="stack">
              <div className="eyebrow">首次使用</div>
              <h2>{currentIndex === 0 ? "连接你的第一台 Hensun" : currentIndex === 1 ? "已认领，等待设备连上服务" : "设备已准备好，试着说声你好"}</h2>
              <p className="muted" style={{ margin: 0 }}>系统会根据真实设备状态，只告诉你当前最需要完成的操作。</p>
            </div>
            <BrandFace />
          </div>
          <div className="checklist" style={{ marginTop: 16 }}>
            {journey.map(([key, title, description], index) => {
              const done = currentIndex >= 0 && index < currentIndex;
              const current = key === data.onboarding.next_action;
              const href = key === "bind_device" ? "/setup" : key === "bring_device_online" ? "/setup?mode=wifi" : "/console/devices";
              return (
                <div className={`checklist-item${done ? " done" : ""}${current ? " current" : ""}`} key={key}>
                  <div className="checklist-number">{done ? <CheckIcon size={17} weight="bold" /> : index + 1}</div>
                  <div className="split"><div><strong>{title}</strong><div className="hint">{description}</div></div>{current ? <Link className="button secondary" href={href}>现在去做 <ArrowRightIcon size={17} /></Link> : null}</div>
                </div>
              );
            })}
          </div>
        </section>
      ) : (
        <section className="card hero-panel split" style={{ marginBottom: 18 }}>
          <div className="stack">
            <span className={`status ${currentDevice?.online ? "online" : "warning"}`}>{currentDevice?.online ? "可以聊天" : "设备离线"}</span>
            <h2>{currentDevice?.online ? `“${currentAgent?.name ?? "助手"}”正在等你` : "先让设备重新连上网络"}</h2>
            <p className="muted" style={{ margin: 0 }}>{currentDevice?.online ? "说“你好小灿”开始对话。助手名称、性格和声音都可以稍后再调整。" : "请确认设备通电，再按联网恢复步骤操作。"}</p>
            <div><Link className="button" href={currentDevice?.online ? "/console/agents" : "/setup?mode=wifi"}>{currentDevice?.online ? "调整助手" : "查看恢复步骤"}</Link></div>
          </div>
          <BrandFace />
        </section>
      )}

      <div className="grid">
        <section className="card"><div className="muted">设备状态</div><div className="metric">{data.devices.filter((item) => item.online).length}/{data.devices.length}</div><div>{currentDevice?.online ? "连接正常" : "需要检查连接"}</div></section>
        <section className="card"><div className="muted">当前助手</div><div className="metric" style={{ fontSize: "1.8rem" }}>{currentAgent?.name ?? "尚未设置"}</div><div>由当前使用档案管理</div></section>
        <section className="card"><div className="muted">本月对话</div><div className="metric">{data.usage.voice_turns}</div><div><MicrophoneIcon size={18} /> 免费内测期间仍统计真实用量</div></section>
      </div>
      {updatedAt ? <p className="hint">{paused ? "自动检查已暂停，可手动刷新 · " : ""}最后更新 {updatedAt.toLocaleTimeString()}</p> : null}
    </>
  );
}
