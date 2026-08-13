"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { ErrorMessage } from "@/components/page-state";
import { api } from "@/lib/api";

type Me = { adult_confirmed: boolean; agreements_complete: boolean };
function safeNext(value: string | null) { return value?.startsWith("/") && !value.startsWith("//") ? value : "/console"; }

export default function AuthCompletePage() {
  const router = useRouter();
  const [confirmed, setConfirmed] = useState(false);
  const [terms, setTerms] = useState(false);
  const [privacy, setPrivacy] = useState(false);
  const [ai, setAi] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  useEffect(() => { const target = safeNext(new URLSearchParams(window.location.search).get("next")); api<Me>("/v1/auth/me").then((me) => { if (me.adult_confirmed && me.agreements_complete) router.replace(target); }).catch((reason) => setError(reason instanceof Error ? reason.message : "登录会话无效")); }, [router]);
  async function complete(event: FormEvent) { event.preventDefault(); setError(null); setSubmitting(true); try { await api("/v1/auth/adult-confirmation", { method: "POST", body: JSON.stringify({ confirmed, accepted_terms: terms, accepted_privacy: privacy, acknowledged_ai: ai }) }); router.replace(safeNext(new URLSearchParams(window.location.search).get("next"))); } catch (reason) { setError(reason instanceof Error ? reason.message : "确认失败"); } finally { setSubmitting(false); } }
  return <main className="landing"><form className="hero-card stack" style={{ width: "min(620px, 100%)" }} onSubmit={complete}><div className="eyebrow">首次登录</div><h1 style={{ fontSize: "2.2rem" }}>完成使用确认</h1><p className="muted">Hensun AI 是成人桌面智能设备，不是真人、儿童玩具或心理治疗设备。</p><label className="check"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />我已年满 18 周岁</label><label className="check"><input type="checkbox" checked={ai} onChange={(event) => setAi(event.target.checked)} />我知道正在与 AI 而非真人互动</label><label className="check"><input type="checkbox" checked={privacy} onChange={(event) => setPrivacy(event.target.checked)} />我已阅读并同意 <Link href="/legal/privacy" target="_blank"><u>隐私政策</u></Link></label><label className="check"><input type="checkbox" checked={terms} onChange={(event) => setTerms(event.target.checked)} />我已阅读并同意 <Link href="/legal/terms" target="_blank"><u>用户服务协议</u></Link></label><button className="button" type="submit" disabled={submitting || !(confirmed && terms && privacy && ai)}>{submitting ? "正在确认…" : "确认并进入控制台"}</button><ErrorMessage message={error} /></form></main>;
}
