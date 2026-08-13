"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";

import { ErrorMessage, InlineResult, Loading, SectionError } from "@/components/page-state";
import { api } from "@/lib/api";
import type { Agent, Device, DeviceConfiguration, UsageProfile } from "@/lib/types";

export default function DevicesPage() {
  const [devices, setDevices] = useState<Device[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [profiles, setProfiles] = useState<UsageProfile[]>([]);
  const [configurations, setConfigurations] = useState<Record<string, DeviceConfiguration>>({});
  const [claimCode, setClaimCode] = useState("");
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const load = useCallback(async () => {
    try {
      const [deviceList, agentList, profileList] = await Promise.all([
        api<Device[]>("/v1/devices"), api<Agent[]>("/v1/agents"), api<UsageProfile[]>("/v1/profiles"),
      ]);
      const deviceConfigurations = await Promise.all(
        deviceList.map(async (device) => [device.id, await api<DeviceConfiguration>(`/v1/devices/${device.id}/configuration`)] as const),
      );
      setDevices(deviceList); setAgents(agentList); setProfiles(profileList);
      setConfigurations(Object.fromEntries(deviceConfigurations));
      setReady(true); setUpdatedAt(new Date()); setError(null);
    } catch (reason) {
      setReady(true); setError(reason instanceof Error ? reason.message : "设备状态暂时无法更新");
    }
  }, []);

  useEffect(() => {
    const initial = window.setTimeout(() => void load(), 0);
    const timer = window.setInterval(() => void load(), 5_000);
    return () => { window.clearTimeout(initial); window.clearInterval(timer); };
  }, [load]);

  async function claim(event: FormEvent) {
    event.preventDefault(); setError(null); setMessage(null);
    if (!/^\d{6}$/.test(claimCode)) { setError("请输入设备屏幕显示的 6 位数字"); return; }
    setSubmitting(true);
    try {
      await api("/v1/claims/confirm", { method: "POST", body: JSON.stringify({ claim_code: claimCode }) });
      setClaimCode(""); setMessage("设备绑定成功，现在可以设置助手。\n"); await load();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "绑定失败"); }
    finally { setSubmitting(false); }
  }

  async function update(device: Device, changes: Record<string, unknown>) {
    setError(null); setSubmitting(true);
    try { await api(`/v1/devices/${device.id}`, { method: "PATCH", body: JSON.stringify(changes) }); await load(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "更新失败"); }
    finally { setSubmitting(false); }
  }

  async function saveConfiguration(event: FormEvent<HTMLFormElement>, device: Device) {
    event.preventDefault(); setError(null); setMessage(null); setSubmitting(true);
    const data = new FormData(event.currentTarget);
    try {
      await api(`/v1/devices/${device.id}/configuration`, {
        method: "PATCH",
        body: JSON.stringify({
          speaker_volume: Number(data.get("speaker_volume")),
          screen_brightness: Number(data.get("screen_brightness")),
        }),
      });
      setMessage(device.online ? "设置已发送，等待设备确认。" : "设置已保存，设备上线后会自动应用。");
      await load();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "设备设置保存失败"); }
    finally { setSubmitting(false); }
  }

  if (!ready) return <Loading cards={2} />;
  return (
    <>
      <header className="page-head"><div><div className="eyebrow">Devices</div><h1>设备</h1><p>查看能否聊天、绑定新设备，并管理当前使用者。</p></div></header>
      <ErrorMessage message={error} />
      {error && devices.length ? <SectionError message={error} onRetry={() => void load()} updatedAt={updatedAt} /> : null}
      {message ? <InlineResult>{message}</InlineResult> : null}
      <div className="grid two" style={{ marginTop: 18 }}>
        <form className="card stack" onSubmit={claim}>
          <h2>绑定新设备</h2>
          <p className="muted">设备联网后会显示一个 10 分钟有效的代码。Wi‑Fi 密码只保存在设备里。</p>
          <div className="field"><label htmlFor="claim">6 位绑定码</label><input id="claim" inputMode="numeric" autoComplete="one-time-code" maxLength={6} placeholder="000000" value={claimCode} onChange={(event) => setClaimCode(event.target.value.replace(/\D/g, ""))} /></div>
          <button className="button" type="submit" disabled={submitting}>{submitting ? "正在绑定…" : "确认绑定"}</button>
        </form>
        {devices.length === 0 ? <section className="card soft"><h2>还没有设备</h2><p className="muted">先完成设备配网，再输入屏幕上的绑定码。</p></section> : null}
        {devices.map((device) => {
          const profile = profiles.find((item) => item.id === device.active_profile_id);
          const configuration = configurations[device.id];
          return (
            <section className="card stack" key={device.id}>
              <div className="split"><h2>{device.name}</h2><span className={`status ${device.online ? "online" : "warning"}`}>{device.online ? "在线，可以聊天" : "离线"}</span></div>
              {!device.online ? <InlineResult tone="warning">请确认本机服务正在运行、设备与电脑连接同一 Wi‑Fi，然后重启设备。</InlineResult> : null}
              <div className="field"><label htmlFor={`agent-${device.id}`}>当前助手</label><select id={`agent-${device.id}`} value={device.active_agent_id ?? ""} disabled={submitting} onChange={(event) => void update(device, { active_agent_id: event.target.value })}>{agents.map((agent) => <option key={agent.id} value={agent.id}>{agent.name}</option>)}</select></div>
              <div><span className="label">当前使用者</span><p style={{ margin: "5px 0 0" }}>{profile?.display_name ?? "本人"} <span className="hint">· {profile?.kind === "youth" ? "家庭档案" : "成人档案"}</span></p></div>
              {configuration ? <form className="card soft stack" key={configuration.desired_version} onSubmit={(event) => void saveConfiguration(event, device)}>
                <div className="split"><h3>声音与屏幕</h3><span className={`status ${configuration.sync_status === "synced" ? "online" : "warning"}`}>{configuration.sync_status === "synced" ? `已生效 v${configuration.applied_version}` : configuration.sync_status === "failed" ? "应用失败" : configuration.sync_status === "pending" ? "等待设备确认" : "尚未确认"}</span></div>
                {configuration.sync_status === "pending" ? <div className="hint">{device.online ? "配置已经下发，收到设备回执后会显示已生效。" : "设备离线；配置已排队，上线后自动下发。"}</div> : null}
                {configuration.sync_status === "failed" ? <InlineResult tone="warning">设备拒绝了这次设置：{configuration.last_error_code ?? "未知原因"}。请恢复到有效范围后重试。</InlineResult> : null}
                <div className="field"><label htmlFor={`volume-${device.id}`}>扬声器音量（10–100）</label><input id={`volume-${device.id}`} name="speaker_volume" type="number" min="10" max="100" defaultValue={configuration.speaker_volume} /></div>
                <div className="field"><label htmlFor={`brightness-${device.id}`}>屏幕亮度（10–100）</label><input id={`brightness-${device.id}`} name="screen_brightness" type="number" min="10" max="100" defaultValue={configuration.screen_brightness} /></div>
                <button className="button secondary" type="submit" disabled={submitting}>{submitting ? "正在保存…" : "保存并下发"}</button>
              </form> : null}
              <details className="details"><summary>设备信息</summary><div className="stack hint"><div className="mono">SN {device.serial_number}</div><div>固件 {device.firmware_version} · 硬件 {device.hardware_version}</div><label className="check"><input type="checkbox" checked={device.ota_auto_update} disabled={submitting} onChange={(event) => void update(device, { ota_auto_update: event.target.checked })} />自动接收灰度更新</label></div></details>
            </section>
          );
        })}
      </div>
      {updatedAt ? <p className="hint">每 5 秒自动检查 · 最后更新 {updatedAt.toLocaleTimeString()}</p> : null}
    </>
  );
}
