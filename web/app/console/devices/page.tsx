"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { ErrorMessage, InlineResult, Loading, SectionError } from "@/components/page-state";
import { api } from "@/lib/api";
import { CLAIM_CODE_KEY, CLAIM_DEVICE_KEY, CLAIM_PENDING_KEY } from "@/lib/onboarding";
import type { Agent, Device, DeviceConfiguration, UsageProfile } from "@/lib/types";

export default function DevicesPage() {
  const router = useRouter();
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
  const [refresh, setRefresh] = useState(0);
  const [paused, setPaused] = useState(false);

  const load = useCallback(async (isCurrent: () => boolean = () => true) => {
    try {
      const [deviceList, agentList, profileList] = await Promise.all([
        api<Device[]>("/v1/devices"), api<Agent[]>("/v1/agents"), api<UsageProfile[]>("/v1/profiles"),
      ]);
      const deviceConfigurations = await Promise.all(
        deviceList.map(async (device) => [device.id, await api<DeviceConfiguration>(`/v1/devices/${device.id}/configuration`)] as const),
      );
      if (!isCurrent()) return;
      setDevices(deviceList); setAgents(agentList); setProfiles(profileList);
      setConfigurations(Object.fromEntries(deviceConfigurations));
      setReady(true); setUpdatedAt(new Date()); setError(null);
    } catch (reason) {
      if (!isCurrent()) return;
      setReady(true); setError(reason instanceof Error ? reason.message : "设备状态暂时无法更新");
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

  function claim(event: FormEvent) {
    event.preventDefault(); setError(null); setMessage(null);
    if (!/^\d{6}$/.test(claimCode)) { setError("请输入设备屏幕显示的 6 位数字"); return; }
    setSubmitting(true);
    try {
      window.sessionStorage.setItem(CLAIM_CODE_KEY, claimCode);
      window.sessionStorage.removeItem(CLAIM_DEVICE_KEY);
      window.sessionStorage.removeItem(CLAIM_PENDING_KEY);
      router.push("/claim");
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

  async function unbind(device: Device) {
    const confirmed = window.confirm(
      `确认解除“${device.name}”的绑定吗？解除后本账户将无法管理设备，设备可由新用户重新绑定。`,
    );
    if (!confirmed) return;
    setError(null); setMessage(null); setSubmitting(true);
    try {
      await api(`/v1/devices/${device.id}/unbind`, { method: "POST" });
      setMessage("设备已恢复为待新用户绑定。寄出前还需清除设备保存的 Wi‑Fi。");
      await load();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "解除绑定失败"); }
    finally { setSubmitting(false); }
  }

  if (!ready) return <Loading cards={2} />;
  return (
    <>
      <header className="page-head"><div><div className="eyebrow">Devices</div><h1>设备</h1><p>查看能否聊天、绑定新设备，并管理当前使用者。</p></div><div className="page-actions"><Link className="button secondary" href="/setup">开始使用 / 添加设备</Link><button className="button secondary" type="button" onClick={() => setRefresh((value) => value + 1)}>刷新设备状态</button></div></header>
      <ErrorMessage message={error} />
      {error && devices.length ? <SectionError message={error} onRetry={() => setRefresh((value) => value + 1)} updatedAt={updatedAt} /> : null}
      {message ? <InlineResult>{message}</InlineResult> : null}
      <div className="grid two" style={{ marginTop: 18 }}>
        <form className="card stack" onSubmit={claim}>
          <h2>绑定新设备</h2>
          <p className="muted">设备联网后会显示一个 10 分钟有效的绑定二维码。请优先用手机扫码；Wi‑Fi 密码只保存在设备里。</p>
          <div className="field"><label htmlFor="claim">扫码失败时，输入 6 位备用码</label><input id="claim" inputMode="numeric" autoComplete="one-time-code" maxLength={6} placeholder="000000" value={claimCode} onChange={(event) => setClaimCode(event.target.value.replace(/\D/g, ""))} /></div>
          <button className="button" type="submit" disabled={submitting}>{submitting ? "正在绑定…" : "确认绑定"}</button>
        </form>
        {devices.length === 0 ? <section className="card soft"><h2>还没有设备</h2><p className="muted">先完成设备配网，再扫描屏幕上的二维码；备用码仅用于扫码失败。</p></section> : null}
        {devices.map((device) => {
          const profile = profiles.find((item) => item.id === device.active_profile_id);
          const configuration = configurations[device.id];
          return (
            <section className="card stack" key={device.id}>
              <div className="split"><h2>{device.name}</h2><span className={`status ${device.online ? "online" : "warning"}`}>{device.online ? "在线，可以聊天" : "离线"}</span></div>
              {!device.online ? <InlineResult tone="warning">设备暂时离线。请确认通电，或按恢复步骤重新联网。</InlineResult> : null}
              <div className="page-actions"><Link className="button secondary" href="/setup?mode=wifi">更换 Wi-Fi</Link><Link className="button secondary" href={`/console?device_id=${encodeURIComponent(device.id)}`}>查看使用进度</Link></div>
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
              <div className="card soft stack">
                <div><strong>交给其他用户</strong><p className="hint" style={{ marginBottom: 0 }}>解除绑定会清除本账户对设备的归属和记忆授权，新用户可用自己的邮箱重新绑定。</p></div>
                <button className="button danger" type="button" disabled={submitting} onClick={() => void unbind(device)}>{submitting ? "正在处理…" : "解除绑定"}</button>
              </div>
            </section>
          );
        })}
      </div>
      {updatedAt ? <p className="hint">{paused ? "自动检查已暂停，可手动刷新" : "每 5 秒检查，最多持续约 2 分半钟"} · 最后更新 {updatedAt.toLocaleTimeString()}</p> : null}
    </>
  );
}
