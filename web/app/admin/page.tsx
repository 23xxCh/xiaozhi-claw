"use client";

import { FormEvent, useState } from "react";

import { ErrorMessage, InlineResult } from "@/components/page-state";
import { api } from "@/lib/api";

type Overview = { role: string; users: number; devices: number; owned_devices: number };
type AdminDevice = { id: string; serial_number: string; board_type: string; lifecycle: string; firmware_version: string; runtime_state: string; runtime_state_at: string | null; runtime_reason: string | null };
type Audit = { id: string; actor_type: string; action: string; created_at: string };
type ModelRoute = { id: string; display_name: string; asr_model: string; llm_model: string; tts_model: string; enabled: boolean; asr_cost_micros_per_minute: number; llm_input_cost_micros_per_million_tokens: number; llm_output_cost_micros_per_million_tokens: number; tts_cost_micros_per_10k_chars: number };
type ProviderLatency = { operation: string; requests: number; average_latency_ms: number; cost_micros: number; errors: number; fallbacks: number };
type OperationsMetrics = { voice_turns_24h: number; active_users_7d: number; active_devices_7d: number; online_devices: number; stale_online_sessions: number; unfinished_conversations: number; first_audio_p50_ms: number | null; first_audio_p95_ms: number | null; provider_requests_30d: number; provider_errors_30d: number; provider_fallbacks_30d: number; provider_cost_micros_30d: number; cost_per_active_user_micros_30d: number; provider_latency: ProviderLatency[]; firmware_versions: Array<{ version: string; devices: number }> };

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
  const [partialErrors, setPartialErrors] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);

  async function loadDashboard() {
    setLoading(true); setPartialErrors([]);
    try {
      const summary = await api<Overview>("/v1/admin/overview"); setOverview(summary);
      const requests = [
        ["设备", api<AdminDevice[]>("/v1/admin/devices"), setDevices],
        ["审计", api<Audit[]>("/v1/admin/audit"), setAudit],
        ["模型", api<ModelRoute[]>("/v1/admin/model-presets"), setModels],
        ["指标", api<OperationsMetrics>("/v1/admin/metrics"), setMetrics],
      ] as const;
      const results = await Promise.allSettled(requests.map(([, request]) => request));
      const failures: string[] = [];
      results.forEach((result, index) => { if (result.status === "fulfilled") requests[index][2](result.value as never); else failures.push(`${requests[index][0]}数据未更新`); });
      setPartialErrors(failures);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "后台加载失败"); }
    finally { setLoading(false); }
  }

  async function login(event: FormEvent) { event.preventDefault(); setError(null); setLoading(true); try { await api("/v1/admin/auth/login", { method: "POST", headers: { "X-Admin-Key": bootstrapKey }, body: JSON.stringify({ username }) }); setBootstrapKey(""); await loadDashboard(); } catch (reason) { setError(reason instanceof Error ? reason.message : "登录失败"); setLoading(false); } }
  async function registerBatch(event: FormEvent) { event.preventDefault(); setError(null); setOneTimeManifest(null); const serials = batchSerials.split(/\r?\n/).map((item) => item.trim()).filter(Boolean); if (!serials.length || !window.confirm(`确认注册 ${serials.length} 台设备并生成一次性密钥？`)) return; try { const result = await api<Array<{ serial_number: string; device_secret: string }>>("/v1/admin/devices/batches", { method: "POST", body: JSON.stringify({ devices: serials.map((serial_number) => ({ serial_number, board_type: "hensun-cam-pilot-v1" })), confirm: true }) }); setOneTimeManifest(["serial_number,device_secret", ...result.map((item) => `${item.serial_number},${item.device_secret}`)].join("\n")); setBatchSerials(""); await loadDashboard(); } catch (reason) { setError(reason instanceof Error ? reason.message : "批次注册失败"); } }
  async function rma(device: AdminDevice) { const reason = window.prompt("输入 RMA 原因（此操作会解绑并隔离设备）"); if (!reason || !window.confirm(`确认隔离 ${device.serial_number}？`)) return; try { await api(`/v1/admin/devices/${device.id}/rma`, { method: "POST", body: JSON.stringify({ reason, confirm: true }) }); await loadDashboard(); } catch (failure) { setError(failure instanceof Error ? failure.message : "RMA 操作失败"); } }
  function downloadManifest() { if (!oneTimeManifest) return; const url = URL.createObjectURL(new Blob([oneTimeManifest], { type: "text/csv;charset=utf-8" })); const link = document.createElement("a"); link.href = url; link.download = `hensun-factory-${Date.now()}.csv`; link.click(); URL.revokeObjectURL(url); setOneTimeManifest(null); }

  if (!overview) return <main className="ops-shell landing"><section className="hero-card stack" style={{ width: "min(560px, 100%)" }}><div className="eyebrow">Hensun Operations</div><h1 style={{ fontSize: "2.2rem" }}>员工工作台</h1><p className="muted">研发、客服和工厂使用独立权限。初始化凭证只保留在本次提交内存中。</p><form className="stack" onSubmit={login}><div className="field"><label htmlFor="staff">员工用户名</label><input id="staff" value={username} onChange={(event) => setUsername(event.target.value)} /></div><div className="field"><label htmlFor="bootstrap-key">初始化管理凭证</label><input id="bootstrap-key" type="password" value={bootstrapKey} onChange={(event) => setBootstrapKey(event.target.value)} /></div><button className="button" type="submit" disabled={loading}>{loading ? "正在登录…" : "登录工作台"}</button><ErrorMessage message={error} /></form></section></main>;

  const canFactory = ["factory", "superadmin"].includes(overview.role);
  const canRma = ["support", "superadmin"].includes(overview.role);
  const offlineDevices = metrics ? Math.max(overview.owned_devices - metrics.online_devices, 0) : 0;
  return <div className="ops-shell"><header className="ops-topbar"><div><strong>Hensun Operations</strong><div className="muted">{overview.role}</div></div><a className="button secondary" href="/console">客户控制台</a></header><main className="ops-main stack">
    <header className="page-head"><div><div className="eyebrow">Work queue</div><h1>需要处理</h1><p>先处理影响用户和设备的异常，再查看趋势和明细。</p></div><button className="button secondary" type="button" disabled={loading} onClick={() => void loadDashboard()}>{loading ? "更新中…" : "刷新"}</button></header>
    <ErrorMessage message={error} />{partialErrors.length ? <InlineResult tone="warning">{partialErrors.join("、")}，其他区块仍可使用。</InlineResult> : null}
    <div className="grid"><section className="card"><div className="muted">Provider 错误</div><div className="metric">{metrics?.provider_errors_30d ?? "—"}</div><div>近 30 天</div></section><section className="card"><div className="muted">旧在线记录 / 未结束会话</div><div className="metric">{metrics ? metrics.stale_online_sessions + metrics.unfinished_conversations : "—"}</div><div>需要自动回收或检查</div></section><section className="card"><div className="muted">离线设备</div><div className="metric">{metrics ? offlineDevices : "—"}</div><div>当前已认领设备</div></section></div>
    {metrics ? <><header className="page-head" style={{ marginTop: 16 }}><div><div className="eyebrow">Operations</div><h2 style={{ fontSize: "2rem" }}>运营健康</h2></div></header><div className="grid"><section className="card"><div className="muted">24 小时语音轮数</div><div className="metric">{metrics.voice_turns_24h}</div><div>7 日活跃用户 {metrics.active_users_7d}</div></section><section className="card"><div className="muted">首段回复 P50 / P95</div><div className="metric">{metrics.first_audio_p50_ms ?? "—"} / {metrics.first_audio_p95_ms ?? "—"}</div><div>毫秒</div></section><section className="card"><div className="muted">30 日模型成本</div><div className="metric">{yuan(metrics.provider_cost_micros_30d)}</div><div>每活跃用户 {yuan(metrics.cost_per_active_user_micros_30d)}</div></section></div></> : null}
    <section className="stack"><h2>设备明细</h2><div className="table-wrap"><table><thead><tr><th>SN</th><th>运行状态</th><th>状态时间</th><th>生命周期</th><th>板型</th><th>固件</th><th>操作</th></tr></thead><tbody>{devices.map((device) => <tr key={device.id}><td className="mono">{device.serial_number}</td><td>{device.runtime_state}{device.runtime_reason ? ` · ${device.runtime_reason}` : ""}</td><td>{device.runtime_state_at ? new Date(device.runtime_state_at).toLocaleString() : "—"}</td><td>{device.lifecycle}</td><td>{device.board_type}</td><td>{device.firmware_version}</td><td>{canRma && device.lifecycle !== "rma-quarantine" ? <button className="button danger" type="button" onClick={() => void rma(device)}>RMA</button> : "—"}</td></tr>)}</tbody></table></div></section>
    {canFactory ? <form className="card stack desktop-only" onSubmit={registerBatch}><h2>工厂批次注册</h2><div className="field"><label htmlFor="serials">每行一个 SN / MAC</label><textarea id="serials" value={batchSerials} onChange={(event) => setBatchSerials(event.target.value)} /></div><button className="button" type="submit">确认并生成一次性烧录清单</button>{oneTimeManifest ? <div className="stack"><InlineResult tone="warning">清单只显示一次，请立即转入受控烧录工位。</InlineResult><button className="button" type="button" onClick={downloadManifest}>下载并清除清单</button></div> : null}</form> : null}
    {models.length ? <section className="card stack desktop-only"><h2>模型路由与目录价</h2>{models.map((model) => <div className="card soft" key={model.id}><strong>{model.display_name}</strong><div className="muted mono">{model.asr_model} → {model.llm_model} → {model.tts_model}</div><div className="muted">ASR {yuan(model.asr_cost_micros_per_minute)}/分钟 · LLM {yuan(model.llm_input_cost_micros_per_million_tokens)}/{yuan(model.llm_output_cost_micros_per_million_tokens)}/百万 Token · TTS {yuan(model.tts_cost_micros_per_10k_chars)}/万字</div></div>)}</section> : null}
    <section className="stack"><h2>最近审计</h2><div className="table-wrap"><table><thead><tr><th>时间</th><th>事件</th><th>操作者</th></tr></thead><tbody>{audit.slice(0, 20).map((event) => <tr key={event.id}><td>{new Date(event.created_at).toLocaleString()}</td><td>{event.action}</td><td>{event.actor_type}</td></tr>)}</tbody></table></div></section>
  </main></div>;
}
