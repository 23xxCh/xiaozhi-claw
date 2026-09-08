"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { ErrorMessage, Loading, SectionError } from "@/components/page-state";
import { ThemeToggle } from "@/components/theme-toggle";
import { api } from "@/lib/api";

type Me = { id: string; email: string | null; display_name: string; adult_confirmed: boolean; agreements_complete: boolean };

export default function AccountPage() {
  const router = useRouter();
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [bindingEmail, setBindingEmail] = useState("");
  const [bindingCode, setBindingCode] = useState("");
  const [bindingBusy, setBindingBusy] = useState(false);
  const [bindingNotice, setBindingNotice] = useState("");
  const [loggingOut, setLoggingOut] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      setMe(await api<Me>("/v1/auth/me"));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "账户信息暂时无法加载");
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  async function bindEmail(verify: boolean) {
    setBindingBusy(true);
    setError(null);
    try {
      if (verify) {
        setMe(await api<Me>("/v1/auth/email/bind", { method: "POST", body: JSON.stringify({ email: bindingEmail, code: bindingCode }) }));
        setBindingNotice("邮箱已绑定，设备和权益仍属于当前账户。");
      } else {
        const result = await api<{ debug_code?: string }>("/v1/auth/email/request-code", { method: "POST", body: JSON.stringify({ email: bindingEmail }) });
        setBindingCode(result.debug_code ?? "");
        setBindingNotice(result.debug_code ? "本地测试模式：未实际发信，验证码已填入。" : "验证码已发送，请查看邮箱；60 秒后可重新获取。");
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "邮箱绑定失败");
    } finally {
      setBindingBusy(false);
    }
  }

  async function logout() {
    setLoggingOut(true);
    setError(null);
    try {
      await api("/v1/auth/logout", { method: "POST" });
      router.replace("/login");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "退出失败，请稍后重试");
      setLoggingOut(false);
    }
  }

  if (!me && !error) return <Loading cards={2} />;
  if (!me) return <SectionError message={error ?? "账户信息暂时无法加载"} onRetry={() => void load()} />;
  return <><header className="page-head"><div><div className="eyebrow">Account</div><h1>账户</h1><p>管理登录、显示偏好和公开版本说明。</p></div></header><ErrorMessage message={error} /><p role="status">{bindingNotice}</p><div className="grid two" style={{ marginTop: error ? 18 : 0 }}><section className="card stack"><h2>{me.display_name}</h2>{me.email ? <div>{me.email}</div> : <form className="stack" onSubmit={(event) => { event.preventDefault(); void bindEmail(true); }}>
      <p className="muted">为当前账户绑定邮箱，保留已有设备和权益。</p>
      <label className="field">邮箱<input type="email" required autoComplete="email" value={bindingEmail} onChange={(event) => { setBindingEmail(event.target.value); setBindingCode(""); }} /></label>
      <button className="button secondary" type="button" disabled={bindingBusy || !bindingEmail} onClick={() => void bindEmail(false)}>获取验证码</button>
      <label className="field">验证码<input required inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} value={bindingCode} onChange={(event) => setBindingCode(event.target.value.replace(/\D/g, ""))} /></label>
      <button className="button" type="submit" disabled={bindingBusy || bindingCode.length !== 6}>验证并绑定邮箱</button>
    </form>}<div className="hint mono">账户 {me.id.slice(0, 8)}…</div><span className={`status ${me.agreements_complete ? "online" : "warning"}`}>{me.agreements_complete ? "协议已确认" : "待确认协议"}</span><button className="button danger" type="button" disabled={loggingOut} onClick={() => void logout()}>{loggingOut ? "正在退出…" : "退出登录"}</button></section><section className="card stack"><h2>外观</h2><p className="muted">首次跟随系统，手动选择后会记住。</p><ThemeToggle /></section><section className="card soft" style={{ gridColumn: "1 / -1" }}><h2>当前公开范围</h2><p className="muted">当前版本仅供 18 岁以上成年人使用。家庭模式仍在内部验证，不代表已经向青少年开放。</p></section></div></>;
}
