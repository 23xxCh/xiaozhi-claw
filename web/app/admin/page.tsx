"use client";

import { FormEvent, useState } from "react";

import { api } from "@/lib/api";

type Overview = { role: string; users: number; devices: number; owned_devices: number };
type AdminDevice = { id: string; serial_number: string; board_type: string; lifecycle: string; firmware_version: string };
type Audit = { id: string; actor_type: string; action: string; created_at: string };
type ModelRoute = { id: string; display_name: string; asr_model: string; llm_model: string; tts_model: string; enabled: boolean };

export default function AdminPage() {
  const [username, setUsername] = useState("operator");
  const [bootstrapKey, setBootstrapKey] = useState("");
  const [overview, setOverview] = useState<Overview | null>(null);
  const [devices, setDevices] = useState<AdminDevice[]>([]);
  const [audit, setAudit] = useState<Audit[]>([]);
  const [models, setModels] = useState<ModelRoute[]>([]);
  const [batchSerials, setBatchSerials] = useState("");
  const [oneTimeManifest, setOneTimeManifest] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function loadDashboard() {
    const summary = await api<Overview>("/v1/admin/overview");
    setOverview(summary);
    const [deviceResult, auditResult, modelResult] = await Promise.allSettled([
      api<AdminDevice[]>("/v1/admin/devices"),
      api<Audit[]>("/v1/admin/audit"),
      api<ModelRoute[]>("/v1/admin/model-presets"),
    ]);
    if (deviceResult.status === "fulfilled") setDevices(deviceResult.value);
    if (auditResult.status === "fulfilled") setAudit(auditResult.value);
    if (modelResult.status === "fulfilled") setModels(modelResult.value);
  }

  async function login(event: FormEvent) {
    event.preventDefault(); setError(null);
    try {
      await api("/v1/admin/auth/login", { method: "POST", headers: { "X-Admin-Key": bootstrapKey }, body: JSON.stringify({ username }) });
      setBootstrapKey(""); await loadDashboard();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "登录失败"); }
  }

  async function registerBatch(event: FormEvent) {
    event.preventDefault(); setError(null); setOneTimeManifest(null);
    const serials = batchSerials.split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
    if (!serials.length || !window.confirm(`确认注册 ${serials.length} 台设备并生成一次性密钥？`)) return;
    try {
      const result = await api<Array<{ serial_number: string; device_secret: string }>>("/v1/admin/devices/batches", { method: "POST", body: JSON.stringify({ devices: serials.map((serial_number) => ({ serial_number, board_type: "hensun-cam-pilot-v1" })), confirm: true }) });
      setOneTimeManifest(["serial_number,device_secret", ...result.map((item) => `${item.serial_number},${item.device_secret}`)].join("\n"));
      setBatchSerials(""); await loadDashboard();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "批次注册失败"); }
  }

  async function rma(device: AdminDevice) {
    const reason = window.prompt("输入 RMA 原因（此操作会解绑并隔离设备）");
    if (!reason || !window.confirm(`确认隔离 ${device.serial_number}？`)) return;
    try { await api(`/v1/admin/devices/${device.id}/rma`, { method: "POST", body: JSON.stringify({ reason, confirm: true }) }); await loadDashboard(); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "RMA 操作失败"); }
  }

  function downloadManifest() {
    if (!oneTimeManifest) return;
    const url = URL.createObjectURL(new Blob([oneTimeManifest], { type: "text/csv;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url; link.download = `hensun-factory-${Date.now()}.csv`; link.click();
    URL.revokeObjectURL(url); setOneTimeManifest(null);
  }

  if (!overview) {
    return <main className="landing"><section className="hero-card" style={{ width: "min(720px, 100%)" }}><div className="eyebrow">内部后台</div><h2 style={{ marginTop: 14 }}>研发 / 客服 / 工厂权限入口</h2><form className="stack" onSubmit={login}><p className="muted">生产环境应接企业身份源；初始化 Key 只在本次提交时留在内存，不会保存到 localStorage。</p><div className="field"><label htmlFor="staff">员工用户名</label><input id="staff" value={username} onChange={(event) => setUsername(event.target.value)} /></div><div className="field"><label htmlFor="bootstrap-key">初始化管理 Key</label><input id="bootstrap-key" type="password" value={bootstrapKey} onChange={(event) => setBootstrapKey(event.target.value)} /></div><button className="button" type="submit">登录</button>{error ? <div className="error">{error}</div> : null}</form></section></main>;
  }

  const canFactory = ["factory", "superadmin"].includes(overview.role);
  const canRma = ["support", "superadmin"].includes(overview.role);
  return (
    <main className="main">
      <header className="page-head"><div><div className="eyebrow">内部后台</div><h2>{overview.role}</h2><p>高风险操作需要二次确认并写入审计日志。</p></div><a className="button secondary" href="/console">客户控制台</a></header>
      {error ? <div className="error">{error}</div> : null}
      <div className="grid">
        <section className="card"><div className="muted">用户</div><div className="metric">{overview.users}</div></section>
        <section className="card"><div className="muted">设备</div><div className="metric">{overview.devices}</div></section>
        <section className="card"><div className="muted">已认领</div><div className="metric">{overview.owned_devices}</div></section>
      </div>
      {canFactory ? <form className="card stack" style={{ marginTop: 18 }} onSubmit={registerBatch}><h2>工厂批次注册</h2><div className="field"><label htmlFor="serials">每行一个 SN / MAC</label><textarea id="serials" value={batchSerials} onChange={(event) => setBatchSerials(event.target.value)} /></div><button className="button" type="submit">确认并生成一次性烧录清单</button>{oneTimeManifest ? <div className="stack"><div className="error">只显示这一次，请立即下载并转入受控烧录工位。</div><button className="button" type="button" onClick={downloadManifest}>下载后清除清单</button></div> : null}</form> : null}
      <section className="card stack" style={{ marginTop: 18 }}><h2>设备</h2>{devices.map((device) => <div className="row" key={device.id}><strong>{device.serial_number}</strong><span className="status">{device.lifecycle}</span><span className="muted">{device.firmware_version}</span>{canRma && device.lifecycle !== "rma-quarantine" ? <button className="button danger" onClick={() => rma(device)}>RMA 隔离</button> : null}</div>)}</section>
      {models.length ? <section className="card stack" style={{ marginTop: 18 }}><h2>模型路由（仅研发可见）</h2>{models.map((model) => <div key={model.id}><strong>{model.display_name}</strong><div className="muted">{model.asr_model} → {model.llm_model} → {model.tts_model}</div></div>)}</section> : null}
      {audit.length ? <section className="card stack" style={{ marginTop: 18 }}><h2>最近审计</h2>{audit.slice(0, 20).map((event) => <div className="row" key={event.id}><span>{event.action}</span><span className="muted">{event.actor_type} · {new Date(event.created_at).toLocaleString()}</span></div>)}</section> : null}
    </main>
  );
}
