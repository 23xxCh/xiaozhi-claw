"use client";

import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";

import { api } from "@/lib/api";

type Me = { adult_confirmed: boolean; agreements_complete: boolean };

export default function AuthCompletePage() {
  const router = useRouter();
  const [confirmed, setConfirmed] = useState(false);
  const [terms, setTerms] = useState(false);
  const [privacy, setPrivacy] = useState(false);
  const [ai, setAi] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<Me>("/v1/auth/me")
      .then((me) => { if (me.adult_confirmed && me.agreements_complete) router.replace("/console"); })
      .catch((reason) => setError(reason instanceof Error ? reason.message : "登录会话无效"));
  }, [router]);

  async function complete(event: FormEvent) {
    event.preventDefault(); setError(null);
    try {
      await api("/v1/auth/adult-confirmation", {
        method: "POST",
        body: JSON.stringify({ confirmed, accepted_terms: terms, accepted_privacy: privacy, acknowledged_ai: ai }),
      });
      router.replace("/console");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "确认失败"); }
  }

  return (
    <main className="landing">
      <form className="hero-card stack" style={{ width: "min(620px, 100%)" }} onSubmit={complete}>
        <div className="eyebrow">首次登录</div><h2>完成使用确认</h2>
        <p className="muted">Hensun AI 是成人桌面智能设备，不是真人、儿童玩具或心理治疗设备。</p>
        <label className="row"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />我已年满 18 周岁</label>
        <label className="row"><input type="checkbox" checked={ai} onChange={(event) => setAi(event.target.checked)} />我知道正在与 AI 而非真人互动</label>
        <label className="row"><input type="checkbox" checked={privacy} onChange={(event) => setPrivacy(event.target.checked)} />我已阅读并同意 <Link href="/legal/privacy" target="_blank"><u>隐私政策</u></Link>（默认不保存音频与逐句对话）</label>
        <label className="row"><input type="checkbox" checked={terms} onChange={(event) => setTerms(event.target.checked)} />我已阅读并同意 <Link href="/legal/terms" target="_blank"><u>用户服务协议</u></Link></label>
        <button className="button" type="submit" disabled={!(confirmed && terms && privacy && ai)}>确认并进入控制台</button>
        {error ? <div className="error">{error}</div> : null}
      </form>
    </main>
  );
}
