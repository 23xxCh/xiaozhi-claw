"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import { BrandFace } from "@/components/brand-face";
import { ErrorMessage, InlineResult } from "@/components/page-state";
import { api, ApiError } from "@/lib/api";

const CLAIM_STORAGE_KEY = "hensun_claim_code";

type Me = { agreements_complete: boolean };

export default function ClaimPage() {
  const router = useRouter();
  const started = useRef(false);
  const [code, setCode] = useState("");
  const [state, setState] = useState<"loading" | "done" | "missing" | "error">("loading");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function claim() {
      if (started.current) return;
      started.current = true;

      const fragment = new URLSearchParams(window.location.hash.slice(1));
      const scannedCode = fragment.get("code") ?? "";
      if (/^[0-9]{6}$/.test(scannedCode)) {
        window.sessionStorage.setItem(CLAIM_STORAGE_KEY, scannedCode);
      }
      if (window.location.hash) {
        window.history.replaceState(
          window.history.state,
          "",
          `${window.location.pathname}${window.location.search}`,
        );
      }

      const restoredCode = window.sessionStorage.getItem(CLAIM_STORAGE_KEY) ?? "";
      setCode(restoredCode);
      if (!/^[0-9]{6}$/.test(restoredCode)) {
        setState("missing");
        return;
      }

      try {
        const me = await api<Me>("/v1/auth/me");
        if (!me.agreements_complete) {
          router.replace("/auth/complete?next=%2Fclaim");
          return;
        }
        await api("/v1/claims/confirm", {
          method: "POST",
          body: JSON.stringify({ claim_code: restoredCode }),
        });
        window.sessionStorage.removeItem(CLAIM_STORAGE_KEY);
        setState("done");
      } catch (reason) {
        if (reason instanceof ApiError && [401, 403].includes(reason.status)) return;
        setError(reason instanceof Error ? reason.message : "设备绑定失败，请重新扫码");
        setState("error");
      }
    }

    const timer = window.setTimeout(() => void claim(), 0);
    return () => window.clearTimeout(timer);
  }, [router]);

  return (
    <main className="landing">
      <section className="hero-card stack" style={{ width: "min(520px, 100%)" }}>
        <div className="split">
          <div><div className="eyebrow">Device claim</div><h1 style={{ fontSize: "2.2rem", marginTop: 10 }}>绑定小灿</h1></div>
          <BrandFace />
        </div>
        {state === "loading" ? <InlineResult>正在安全绑定设备{code ? `（备用码 ${code}）` : ""}…</InlineResult> : null}
        {state === "done" ? <div className="stack"><InlineResult tone="success">设备在线，可以聊天</InlineResult><h2>请说：你好小灿</h2><p className="muted">默认助手已准备好。名称、性格和音色以后再设置也可以。</p><Link className="button" href="/console/devices">查看我的设备</Link></div> : null}
        {state === "missing" ? <div className="stack"><InlineResult tone="warning">二维码中没有有效的 6 位绑定码。</InlineResult><p className="muted">请重新扫描设备屏幕上的二维码，或前往设备页面手动输入备用码。</p><Link className="button" href="/console/devices">手动输入备用码</Link></div> : null}
        {state === "error" ? <div className="stack"><ErrorMessage message={error} /><p className="muted">绑定码可能已过期或设备已被认领，请让设备重新显示二维码后再扫一次。</p><Link className="button secondary" href="/console/devices">返回设备页面</Link></div> : null}
      </section>
    </main>
  );
}
