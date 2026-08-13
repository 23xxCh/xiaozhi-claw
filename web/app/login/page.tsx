"use client";

import { ArrowLeftIcon } from "@phosphor-icons/react/dist/ssr/ArrowLeft";
import { EnvelopeSimpleIcon } from "@phosphor-icons/react/dist/ssr/EnvelopeSimple";
import { type FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { BrandFace } from "@/components/brand-face";
import { ErrorMessage, InlineResult } from "@/components/page-state";
import { api } from "@/lib/api";

type RequestCodeResponse = { expires_in: number; resend_after: number; debug_code?: string };
type VerifyCodeResponse = { agreements_complete: boolean };

function safeNext(value: string | null) {
  return value?.startsWith("/") && !value.startsWith("//") ? value : "/console";
}

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState(process.env.NEXT_PUBLIC_DEV_EMAIL ?? "");
  const [code, setCode] = useState("");
  const [codeRequested, setCodeRequested] = useState(false);
  const [debugCode, setDebugCode] = useState<string | null>(null);
  const [resendAfter, setResendAfter] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (resendAfter <= 0) return;
    const timer = window.setInterval(() => setResendAfter((value) => Math.max(0, value - 1)), 1000);
    return () => window.clearInterval(timer);
  }, [resendAfter]);

  async function requestCode(event?: FormEvent) {
    event?.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const result = await api<RequestCodeResponse>("/v1/auth/email/request-code", {
        method: "POST",
        body: JSON.stringify({ email }),
      });
      setCodeRequested(true);
      setResendAfter(result.resend_after);
      setDebugCode(result.debug_code ?? null);
      if (result.debug_code) setCode(result.debug_code);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "验证码发送失败，请稍后重试");
    } finally {
      setSubmitting(false);
    }
  }

  async function verifyCode(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const result = await api<VerifyCodeResponse>("/v1/auth/email/verify-code", {
        method: "POST",
        body: JSON.stringify({ email, code }),
      });
      const target = safeNext(new URLSearchParams(window.location.search).get("next"));
      router.replace(result.agreements_complete ? target : `/auth/complete?next=${encodeURIComponent(target)}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "登录失败，请重新输入验证码");
    } finally {
      setSubmitting(false);
    }
  }

  function changeEmail() {
    setCodeRequested(false);
    setCode("");
    setDebugCode(null);
    setResendAfter(0);
    setError(null);
  }

  return (
    <main className="landing">
      <section className="hero-card stack" style={{ width: "min(480px, 100%)" }}>
        <div className="split">
          <div><div className="eyebrow">Welcome back</div><h1 style={{ fontSize: "2.2rem", marginTop: 10 }}>登录 Hensun AI</h1></div>
          <BrandFace />
        </div>
        <p className="muted">使用邮箱验证码安全登录。当前公开版本仅供 18 岁以上成年人使用。</p>
        {!codeRequested ? (
          <form className="stack" onSubmit={(event) => void requestCode(event)}>
            <label className="field" htmlFor="login-email">邮箱地址
              <input id="login-email" type="email" autoComplete="email" inputMode="email" required value={email} onChange={(event) => setEmail(event.target.value)} placeholder="name@example.com" />
            </label>
            <button className="button" type="submit" disabled={submitting || !email}>{submitting ? "正在发送…" : <><EnvelopeSimpleIcon size={19} />获取验证码</>}</button>
          </form>
        ) : (
          <form className="stack" onSubmit={verifyCode}>
            <div className="split"><div><div className="label">验证码已发送至</div><strong>{email}</strong></div><button className="button ghost" type="button" onClick={changeEmail}><ArrowLeftIcon size={18} />修改邮箱</button></div>
            {debugCode ? <InlineResult tone="warning">本地开发模式没有真实发信。测试验证码已自动填入：<span className="mono">{debugCode}</span></InlineResult> : <InlineResult>请查看邮箱并输入 6 位验证码。</InlineResult>}
            <label className="field" htmlFor="login-code">6 位验证码
              <input id="login-code" className="mono" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} required value={code} onChange={(event) => setCode(event.target.value.replace(/\D/g, ""))} placeholder="000000" />
            </label>
            <button className="button" type="submit" disabled={submitting || code.length !== 6}>{submitting ? "正在验证…" : "验证并登录"}</button>
            <button className="button secondary" type="button" disabled={submitting || resendAfter > 0} onClick={() => void requestCode()}>{resendAfter > 0 ? `${resendAfter} 秒后可重新发送` : "重新发送验证码"}</button>
          </form>
        )}
        <ErrorMessage message={error} />
        <p className="hint">首次登录后还需要确认年龄、AI 提示、隐私政策和服务协议。</p>
      </section>
    </main>
  );
}
