"use client";

import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";

import { BrandFace } from "@/components/brand-face";
import { ErrorMessage } from "@/components/page-state";
import { api } from "@/lib/api";

function safeNext(value: string | null) { return value?.startsWith("/") && !value.startsWith("//") ? value : "/console"; }

export default function LoginPage() {
  const router = useRouter();
  const [openid, setOpenid] = useState(process.env.NEXT_PUBLIC_DEV_OPENID ?? "wx-local-adult");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const developmentLogin = process.env.NEXT_PUBLIC_ENABLE_DEV_LOGIN === "true";

  async function wechatLogin() { setError(null); try { const result = await api<{ authorization_url: string }>("/v1/auth/wechat/start"); window.location.assign(result.authorization_url); } catch (reason) { setError(reason instanceof Error ? reason.message : "微信登录暂不可用"); } }
  async function devLogin(event: FormEvent) { event.preventDefault(); setError(null); setSubmitting(true); try { await api("/v1/auth/dev-login", { method: "POST", body: JSON.stringify({ openid, adult_confirmed: true }) }); router.replace(safeNext(new URLSearchParams(window.location.search).get("next"))); } catch (reason) { setError(reason instanceof Error ? reason.message : "登录失败"); } finally { setSubmitting(false); } }

  return <main className="landing"><section className="hero-card stack" style={{ width: "min(480px, 100%)" }}><div className="split"><div><div className="eyebrow">Welcome back</div><h1 style={{ fontSize: "2.2rem", marginTop: 10 }}>登录 Hensun AI</h1></div><BrandFace /></div><p className="muted">当前公开版本仅供 18 岁以上成年人使用。你正在与 AI，而不是真人互动。</p><button className="button" type="button" onClick={() => void wechatLogin()}>微信扫码登录</button>{developmentLogin ? <form className="stack" onSubmit={devLogin}><div className="divider" /><div className="field"><label htmlFor="openid">本地开发身份</label><input id="openid" value={openid} onChange={(event) => setOpenid(event.target.value)} /></div><button className="button secondary" type="submit" disabled={submitting}>{submitting ? "正在进入…" : "本地开发登录"}</button></form> : null}<ErrorMessage message={error} /></section></main>;
}
