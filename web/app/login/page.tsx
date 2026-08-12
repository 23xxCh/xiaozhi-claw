"use client";

import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";

import { api } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [openid, setOpenid] = useState("wx-local-adult");
  const [error, setError] = useState<string | null>(null);
  const developmentLogin = process.env.NEXT_PUBLIC_ENABLE_DEV_LOGIN === "true";

  async function wechatLogin() {
    setError(null);
    try {
      const result = await api<{ authorization_url: string }>("/v1/auth/wechat/start");
      window.location.assign(result.authorization_url);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "微信登录暂不可用");
    }
  }

  async function devLogin(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      await api("/v1/auth/dev-login", {
        method: "POST",
        body: JSON.stringify({ openid, adult_confirmed: true }),
      });
      router.push("/console");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "登录失败");
    }
  }

  return (
    <main className="landing">
      <section className="hero-card" style={{ width: "min(480px, 100%)" }}>
        <div className="eyebrow">账户登录</div>
        <h2 style={{ marginTop: 14 }}>进入 Hensun AI 控制台</h2>
        <p className="muted">登录即表示你已年满 18 周岁，并知悉当前交互对象是 AI。</p>
        <div className="stack">
          <button className="button" type="button" onClick={wechatLogin}>微信扫码登录</button>
          {developmentLogin ? (
            <form className="stack" onSubmit={devLogin}>
              <div className="divider" />
              <div className="field">
                <label htmlFor="openid">本地开发 OpenID</label>
                <input id="openid" value={openid} onChange={(event) => setOpenid(event.target.value)} />
              </div>
              <button className="button secondary" type="submit">本地开发登录</button>
            </form>
          ) : null}
          {error ? <div className="error">{error}</div> : null}
        </div>
      </section>
    </main>
  );
}
