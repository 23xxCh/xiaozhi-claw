"use client";

import { FormEvent, useEffect, useState } from "react";

import { ErrorMessage, Loading } from "@/components/page-state";
import { api } from "@/lib/api";
import type { Agent, Device } from "@/lib/types";

export default function DevicesPage() {
  const [devices, setDevices] = useState<Device[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [claimCode, setClaimCode] = useState("");
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  async function load() {
    const [deviceList, agentList] = await Promise.all([api<Device[]>("/v1/devices"), api<Agent[]>("/v1/agents")]);
    setDevices(deviceList); setAgents(agentList); setReady(true); setError(null);
  }
  useEffect(() => {
    let cancelled = false;
    async function refresh() {
      try {
        const [deviceList, agentList] = await Promise.all([api<Device[]>("/v1/devices"), api<Agent[]>("/v1/agents")]);
        if (cancelled) return;
        setDevices(deviceList); setAgents(agentList); setReady(true); setError(null);
      } catch (reason) {
        if (!cancelled) setError(reason instanceof Error ? reason.message : "控制面暂不可用");
      }
    }
    void refresh();
    const timer = window.setInterval(refresh, 5_000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, []);

  async function claim(event: FormEvent) {
    event.preventDefault(); setError(null); setMessage(null);
    if (!/^\d{6}$/.test(claimCode)) { setError("请输入设备屏幕显示的 6 位数字"); return; }
    try {
      await api("/v1/claims/confirm", { method: "POST", body: JSON.stringify({ claim_code: claimCode }) });
      setClaimCode(""); setMessage("设备绑定成功"); await load();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "绑定失败"); }
  }

  async function update(device: Device, changes: Record<string, unknown>) {
    setError(null);
    try { await api(`/v1/devices/${device.id}`, { method: "PATCH", body: JSON.stringify(changes) }); await load(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "更新失败"); }
  }

  if (!ready && !error) return <Loading />;
  return (
    <>
      <header className="page-head"><div><h2>设备与绑定</h2><p>Wi‑Fi 密码只写入设备 NVS；网页只处理 6 位账户绑定码。</p></div></header>
      <ErrorMessage message={error} />
      {error ? <button className="button secondary" type="button" onClick={() => void load()}>重新连接</button> : null}
      {message ? <div className="success">{message}</div> : null}
      <div className="grid two" style={{ marginTop: 18 }}>
        <form className="card stack" onSubmit={claim}>
          <h2>绑定新设备</h2>
          <p className="muted">设备联网后会显示并播报一个 10 分钟有效的代码。</p>
          <div className="field"><label htmlFor="claim">6 位绑定码</label><input id="claim" inputMode="numeric" maxLength={6} placeholder="000000" value={claimCode} onChange={(event) => setClaimCode(event.target.value.replace(/\D/g, ""))} /></div>
          <button className="button" type="submit">确认绑定</button>
        </form>
        {devices.map((device) => (
          <section className="card stack" key={device.id}>
            <div className="row"><h2 style={{ margin: 0 }}>{device.name}</h2><span className={`status ${device.online ? "online" : ""}`}>{device.online ? "在线" : "离线"}</span></div>
            <div className="muted">SN {device.serial_number}</div>
            <div>固件 {device.firmware_version} · 硬件 {device.hardware_version}</div>
            <div className="field"><label htmlFor={`agent-${device.id}`}>活动智能体</label><select id={`agent-${device.id}`} value={device.active_agent_id ?? ""} onChange={(event) => update(device, { active_agent_id: event.target.value })}>{agents.map((agent) => <option key={agent.id} value={agent.id}>{agent.name}</option>)}</select></div>
            <label className="row"><input type="checkbox" checked={device.ota_auto_update} onChange={(event) => update(device, { ota_auto_update: event.target.checked })} />自动接收灰度 OTA</label>
          </section>
        ))}
      </div>
    </>
  );
}
