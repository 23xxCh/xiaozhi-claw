"use client";

import Link from "next/link";
import { type FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { BrandFace } from "@/components/brand-face";
import { ErrorMessage, InlineResult } from "@/components/page-state";
import { SetupGuide } from "@/components/setup-guide";
import { api, ApiError } from "@/lib/api";
import { CLAIM_CODE_KEY, CLAIM_DEVICE_KEY, CLAIM_PENDING_KEY } from "@/lib/onboarding";
import type { Device, OnboardingStatus } from "@/lib/types";

type Me = { agreements_complete: boolean };
const authRedirect = (reason: unknown) => reason instanceof ApiError &&
  (reason.status === 401 || (reason.status === 403 && reason.code === "AGREEMENTS_REQUIRED"));
const failureMessage = (reason: unknown) => reason instanceof Error &&
  ["SecurityError", "QuotaExceededError"].includes(reason.name)
  ? "浏览器无法保存本次绑定进度，请允许本站使用会话存储后重试。"
  : reason instanceof Error ? reason.message : "暂时无法确认设备状态，请重试";

export default function ClaimPage() {
  const router = useRouter();
  const started = useRef(false);
  const inFlight = useRef(false);
  const [code, setCode] = useState("");
  const [state, setState] = useState<"loading" | "done" | "missing" | "error">("loading");
  const [error, setError] = useState<string | null>(null);
  const [claimedDeviceId, setClaimedDeviceId] = useState<string | null>(null);
  const [online, setOnline] = useState<boolean | null>(null);
  const [firstConversation, setFirstConversation] = useState(false);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [checkingStatus, setCheckingStatus] = useState(false);
  const [statusRefresh, setStatusRefresh] = useState(0);
  const [paused, setPaused] = useState(false);

  const claim = useCallback(async (value: string) => {
    if (inFlight.current) return;
    if (!/^\d{6}$/.test(value)) { setError("请输入设备屏幕上的 6 位绑定码"); return; }
    inFlight.current = true;
    setState("loading"); setError(null);
    try {
      window.sessionStorage.setItem(CLAIM_CODE_KEY, value);
      window.sessionStorage.removeItem(CLAIM_DEVICE_KEY);
      const me = await api<Me>("/v1/auth/me");
      if (!me.agreements_complete) { router.replace("/auth/complete?next=%2Fclaim"); return; }
      window.sessionStorage.setItem(CLAIM_PENDING_KEY, "true");
      const claimed = await api<Pick<Device, "id">>("/v1/claims/confirm", {
        method: "POST", body: JSON.stringify({ claim_code: value }),
      });
      window.sessionStorage.setItem(CLAIM_DEVICE_KEY, claimed.id);
      window.sessionStorage.removeItem(CLAIM_CODE_KEY);
      window.sessionStorage.removeItem(CLAIM_PENDING_KEY);
      setClaimedDeviceId(claimed.id); setOnline(null); setState("done");
    } catch (reason) {
      try {
        if (reason instanceof ApiError && reason.status >= 400 && reason.status < 500) {
          window.sessionStorage.removeItem(CLAIM_PENDING_KEY);
          if ([404, 409, 410].includes(reason.status)) window.sessionStorage.removeItem(CLAIM_CODE_KEY);
        }
      } catch { /* The original error remains visible when storage is unavailable. */ }
      if (authRedirect(reason)) return;
      setError(failureMessage(reason));
      setState("error");
    } finally { inFlight.current = false; }
  }, [router]);

  useEffect(() => {
    async function resume() {
      if (started.current) return;
      started.current = true;
      try {
      const scanned = new URLSearchParams(window.location.hash.slice(1)).get("code");
      if (window.location.hash) window.history.replaceState(window.history.state, "", `${window.location.pathname}${window.location.search}`);
      if (scanned !== null) {
        if (!/^\d{6}$/.test(scanned)) {
          window.sessionStorage.removeItem(CLAIM_CODE_KEY);
          window.sessionStorage.removeItem(CLAIM_DEVICE_KEY);
          window.sessionStorage.removeItem(CLAIM_PENDING_KEY);
          setError("二维码中的绑定码无效，请使用屏幕上的新码"); setState("missing"); return;
        }
        window.sessionStorage.setItem(CLAIM_CODE_KEY, scanned);
        window.sessionStorage.removeItem(CLAIM_DEVICE_KEY);
        window.sessionStorage.removeItem(CLAIM_PENDING_KEY);
      }
      const restoredCode = window.sessionStorage.getItem(CLAIM_CODE_KEY) ?? "";
      setCode(restoredCode);
      if (window.sessionStorage.getItem(CLAIM_PENDING_KEY)) {
        setError("上次绑定结果尚未确认。请先查看我的设备；确认没有绑定后，再重试。");
        setState("error"); return;
      }
      if (/^\d{6}$/.test(restoredCode)) { await claim(restoredCode); return; }
      const deviceId = window.sessionStorage.getItem(CLAIM_DEVICE_KEY);
      if (!deviceId) { setState("missing"); return; }
      try {
        const devices = await api<Device[]>("/v1/devices");
        const owned = devices.find((device) => device.id === deviceId);
        if (!owned) {
          window.sessionStorage.removeItem(CLAIM_DEVICE_KEY);
          setError("当前账户没有这台设备，请重新扫码或查看我的设备"); setState("missing"); return;
        }
        setClaimedDeviceId(deviceId); setOnline(owned.online === true); setState("done");
      } catch (reason) {
        if (authRedirect(reason)) return;
        setError(failureMessage(reason)); setState("error");
      }
      } catch (reason) { setError(failureMessage(reason)); setState("error"); }
    }
    const timer = window.setTimeout(() => void resume(), 0);
    return () => window.clearTimeout(timer);
  }, [claim]);

  useEffect(() => {
    if (!claimedDeviceId) return;
    let active = true;
    let timer: number;
    let attempts = 0;
    async function refreshStatus() {
      attempts += 1; setCheckingStatus(true); setPaused(false);
      let completed = false;
      try {
        const devices = await api<Device[]>("/v1/devices");
        if (!active) return;
        const device = devices.find((item) => item.id === claimedDeviceId);
        if (!device) {
          window.sessionStorage.removeItem(CLAIM_DEVICE_KEY);
          setClaimedDeviceId(null); setOnline(null); setState("missing");
          setError("这台设备已不属于当前账户，请重新确认绑定"); return;
        }
        setOnline(device.online === true); setStatusError(null);
        if (device.online) {
          const onboarding = await api<OnboardingStatus>(`/v1/onboarding/status?device_id=${encodeURIComponent(claimedDeviceId!)}`);
          if (!active) return;
          completed = onboarding.first_conversation_complete;
          setFirstConversation(completed);
        }
      } catch (reason) {
        if (!active) return;
        setOnline(null);
        setStatusError(reason instanceof Error ? reason.message : "设备状态查询失败");
      } finally {
        if (active) {
          setCheckingStatus(false);
          if (!completed && attempts < 30) timer = window.setTimeout(() => void refreshStatus(), 3000);
          else if (!completed) setPaused(true);
        }
      }
    }
    timer = window.setTimeout(() => void refreshStatus(), 0);
    return () => { active = false; window.clearTimeout(timer); };
  }, [claimedDeviceId, statusRefresh]);

  function submit(event: FormEvent) { event.preventDefault(); void claim(code); }

  return <main className="landing"><section className="hero-card stack" style={{ width: "min(620px, 100%)" }}>
    <div className="split"><div><div className="eyebrow">Device claim</div><h1 style={{ fontSize: "2.2rem", marginTop: 10 }}>绑定小灿</h1></div><BrandFace /></div>
    {state === "loading" ? <InlineResult>正在确认登录与设备绑定…</InlineResult> : null}
    {state === "done" ? <div className="stack">
      {online === true ? <><InlineResult tone="success">设备在线，可以聊天</InlineResult><h2>{firstConversation ? "首次对话已完成" : "请说：你好小灿"}</h2><p className="muted">{firstConversation ? "现在可以按自己的喜好调整助手。" : "也可以短按 BOOT 开始对话。名称、性格和音色以后再设置也可以。"}</p><Link className="button" href="/console/agents">调整助手和语音方案</Link></> : <>
        <InlineResult tone="warning">{online === false ? "设备已绑定，等待设备上线" : "设备已绑定，正在确认在线状态"}</InlineResult>
        <p className="muted">请确认设备已通电并连接 Wi-Fi。只有服务器确认在线后，才会提示可以聊天。</p>
        <Link className="button secondary" href="/setup?mode=wifi">查看联网恢复步骤</Link>
      </>}
      <ErrorMessage message={statusError} />
      {paused ? <p className="hint">自动检查已暂停。网络恢复后可以手动刷新。</p> : null}
      {!firstConversation || online !== true ? <button className="button secondary" type="button" disabled={checkingStatus} onClick={() => setStatusRefresh((value) => value + 1)}>{checkingStatus ? "正在刷新…" : "刷新设备状态"}</button> : null}
      <Link className="button secondary" href={`/console?device_id=${encodeURIComponent(claimedDeviceId ?? "")}`}>查看这台设备的使用进度</Link>
      <Link className="button secondary" href="/console/devices">查看我的设备</Link>
    </div> : null}
    {state === "missing" || state === "error" ? <>
      <ErrorMessage message={error} />
      <form className="stack" onSubmit={submit}><label className="field" htmlFor="claim-code">设备屏幕上的 6 位备用码<input id="claim-code" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} required value={code} onChange={(event) => setCode(event.target.value.replace(/\D/g, ""))} /></label><button className="button" type="submit" disabled={code.length !== 6}>{state === "error" ? "重试绑定" : "登录并继续绑定"}</button></form>
      {state === "error" ? <><p className="hint">码已过期时，请用设备屏幕上的新码。若提示已被认领，先查看我的设备；属于其他账户时应由原主人解除绑定。</p><Link className="button secondary" href="/console/devices">查看我的设备</Link><button className="button secondary" type="button" onClick={() => window.location.reload()}>重新检查状态</button></> : <SetupGuide />}
    </> : null}
  </section></main>;
}
