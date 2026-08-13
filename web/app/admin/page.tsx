"use client";

import { FormEvent, useState } from "react";

import { api } from "@/lib/api";

type Overview = { role: string; users: number; devices: number; owned_devices: number };
type AdminDevice = { id: string; serial_number: string; board_type: string; lifecycle: string; firmware_version: string };
type Audit = { id: string; actor_type: string; action: string; created_at: string };
type ModelRoute = {
  id: string;
  display_name: string;
  asr_model: string;
  llm_model: string;
  tts_model: string;
  enabled: boolean;
  asr_cost_micros_per_minute: number;
  llm_input_cost_micros_per_million_tokens: number;
  llm_output_cost_micros_per_million_tokens: number;
  tts_cost_micros_per_10k_chars: number;
};
type ProviderLatency = { operation: string; requests: number; average_latency_ms: number; cost_micros: number; errors: number; fallbacks: number };
type OperationsMetrics = {
  voice_turns_24h: number;
  active_users_7d: number;
  active_devices_7d: number;
  online_devices: number;
  stale_online_sessions: number;
  unfinished_conversations: number;
  first_audio_p50_ms: number | null;
  first_audio_p95_ms: number | null;
  provider_requests_30d: number;
  provider_errors_30d: number;
  provider_fallbacks_30d: number;
  provider_cost_micros_30d: number;
  cost_per_active_user_micros_30d: number;
  provider_latency: ProviderLatency[];
  firmware_versions: Array<{ version: string; devices: number }>;
};

function yuan(micros: number) { return `¥${(micros / 1_000_000).toFixed(4)}`; }

export default function AdminPage() {
  const [username, setUsername] = useState("operator");
  const [bootstrapKey, setBootstrapKey] = useState("");
  const [overview, setOverview] = useState<Overview | null>(null);
  const [devices, setDevices] = useState<AdminDevice[]>([]);
  const [audit, setAudit] = useState<Audit[]>([]);
  const [models, setModels] = useState<ModelRoute[]>([]);
  const [metrics, setMetrics] = useState<OperationsMetrics | null>(null);
  const [batchSerials, setBatchSerials] = useState("");
  const [oneTimeManifest, setOneTimeManifest] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function loadDashboard() {
    const summary = await api<Overview>("/v1/admin/overview");
    setOverview(summary);
    const [deviceResult, auditResult, modelResult, metricsResult] = await Promise.allSettled([
      api<AdminDevice[]>("/v1/admin/devices"),
      api<Audit[]>("/v1/admin/audit"),
      api<ModelRoute[]>("/v1/admin/model-presets"),
      api<OperationsMetrics>("/v1/admin/metrics"),
    ]);
    if (deviceResult.status === "fulfilled") setDevices(deviceResult.value);
    if (auditResult.status === "fulfilled") setAudit(auditResult.value);
    if (modelResult.status === "fulfilled") setModels(modelResult.value);
    if (metricsResult.status === "fulfilled") setMetrics(metricsResult.value);
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
      {metrics ? <>
        <header className="page-head" style={{ marginTop: 28 }}><div><h2>运营数据</h2><p>在线、活跃、响应速度、降级和模型成本均来自自有网关。</p></div><button className="button secondary" type="button" onClick={loadDashboard}>刷新数据</button></header>
        <div className="grid">
          <section className="card"><div className="muted">当前在线设备</div><div className="metric">{metrics.online_devices}</div><div>7日活跃设备 {metrics.active_devices_7d}</div></section>
          <section className="card"><div className="muted">24小时语音轮数</div><div className="metric">{metrics.voice_turns_24h}</div><div>7日活跃用户 {metrics.active_users_7d}</div></section>
          <section className="card"><div className="muted">首段回复 P50 / P95</div><div className="metric">{metrics.first_audio_p50_ms ?? "—"} / {metrics.first_audio_p95_ms ?? "—"}</div><div>毫秒，目标 P50 ≤ 2500</div></section>
          <section className="card"><div className="muted">30日模型成本</div><div className="metric">{yuan(metrics.provider_cost_micros_30d)}</div><div>每活跃用户 {yuan(metrics.cost_per_active_user_micros_30d)}</div></section>
        </div>
        <section className="card stack" style={{ marginTop: 18 }}>
          <h2>运行健康</h2>
          <div className="row"><span>Provider 调用</span><strong>{metrics.provider_requests_30d}</strong><span>错误 {metrics.provider_errors_30d}</span><span>自动降级 {metrics.provider_fallbacks_30d}</span></div>
          <div className="row"><span>待回收在线记录</span><strong>{metrics.stale_online_sessions}</strong><span>未结束旧会话</span><strong>{metrics.unfinished_conversations}</strong></div>
          {metrics.provider_latency.map((item) => <div className="row" key={item.operation}><strong>{item.operation.toUpperCase()}</strong><span>平均 {item.average_latency_ms} ms</span><span>{item.requests} 次</span><span>降级 {item.fallbacks}</span><span>{yuan(item.cost_micros)}</span></div>)}
          {metrics.firmware_versions.map((item) => <div className="row" key={item.version}><strong>固件 {item.version}</strong><span>{item.devices} 台设备</span></div>)}
        </section>
      </> : null}
      {canFactory ? <form className="card stack" style={{ marginTop: 18 }} onSubmit={registerBatch}><h2>工厂批次注册</h2><div className="field"><label htmlFor="serials">每行一个 SN / MAC</label><textarea id="serials" value={batchSerials} onChange={(event) => setBatchSerials(event.target.value)} /></div><button className="button" type="submit">确认并生成一次性烧录清单</button>{oneTimeManifest ? <div className="stack"><div className="error">只显示这一次，请立即下载并转入受控烧录工位。</div><button className="button" type="button" onClick={downloadManifest}>下载后清除清单</button></div> : null}</form> : null}
      <section className="card stack" style={{ marginTop: 18 }}><h2>设备</h2>{devices.map((device) => <div className="row" key={device.id}><strong>{device.serial_number}</strong><span className="status">{device.lifecycle}</span><span className="muted">{device.firmware_version}</span>{canRma && device.lifecycle !== "rma-quarantine" ? <button className="button danger" onClick={() => rma(device)}>RMA 隔离</button> : null}</div>)}</section>
      {models.length ? <section className="card stack" style={{ marginTop: 18 }}><h2>模型路由与目录价（仅研发可见）</h2>{models.map((model) => <div key={model.id}><strong>{model.display_name}</strong><div className="muted">{model.asr_model} → {model.llm_model} → {model.tts_model}</div><div className="muted">ASR {yuan(model.asr_cost_micros_per_minute)}/分钟 · LLM {yuan(model.llm_input_cost_micros_per_million_tokens)}/{yuan(model.llm_output_cost_micros_per_million_tokens)}/百万Token · TTS {yuan(model.tts_cost_micros_per_10k_chars)}/万字</div></div>)}</section> : null}
      {audit.length ? <section className="card stack" style={{ marginTop: 18 }}><h2>最近审计</h2>{audit.slice(0, 20).map((event) => <div className="row" key={event.id}><span>{event.action}</span><span className="muted">{event.actor_type} · {new Date(event.created_at).toLocaleString()}</span></div>)}</section> : null}
    </main>
  );
}
