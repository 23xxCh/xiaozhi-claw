import Link from "next/link";
import { BrandFace } from "@/components/brand-face";
import { SetupGuide } from "@/components/setup-guide";

export default async function SetupPage({ searchParams }: { searchParams: Promise<{ mode?: string }> }) {
  const wifiOnly = (await searchParams).mode === "wifi";
  return <main className="landing"><section className="hero-card stack" style={{ width: "min(620px, 100%)" }}>
    <div className="split"><h1 style={{ fontSize: "2rem" }}>{wifiOnly ? "恢复设备联网" : "开始使用 Hensun"}</h1><BrandFace /></div>
    <SetupGuide wifiOnly={wifiOnly} />
    {!wifiOnly ? <><Link className="button" href="/claim">我已有绑定码</Link><Link className="button secondary" href="/console/devices">设备已绑定，进入控制台</Link></> : null}
  </section></main>;
}
