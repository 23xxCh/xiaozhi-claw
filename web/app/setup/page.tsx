import Link from "next/link";
import { BrandFace } from "@/components/brand-face";
import { SetupGuide } from "@/components/setup-guide";

export default async function SetupPage({ searchParams }: { searchParams: Promise<{ mode?: string }> }) {
  const wifiOnly = (await searchParams).mode === "wifi";
  return <main className="setup-page"><header className="setup-header"><Link className="brand" href="/console"><BrandFace small />Hensun AI</Link><Link className="button secondary" href="/console/devices">我的设备</Link></header><div className="setup-layout"><aside className="setup-overview"><div className="eyebrow">设备连接指南</div><h1>{wifiOnly ? "恢复设备联网" : "让小灿来到你身边"}</h1><p className="muted">一部手机，一台设备。按照屏幕提示，一步一步完成连接。</p><div className="setup-device"><BrandFace /><span>HENSUN DESK</span></div><ol className="setup-milestones"><li><strong>连接 Wi-Fi</strong><span>手机直连设备，设置家庭网络</span></li><li><strong>{wifiOnly ? "保留账户绑定" : "认领到你的账户"}</strong><span>{wifiOnly ? "换网不需要解绑，也不清空角色" : "扫描设备二维码，或输入绑定码"}</span></li><li><strong>确认在线，开始聊天</strong><span>选择助手，说“你好小灿”</span></li></ol></aside><section className="card stack setup-panel">
    <SetupGuide wifiOnly={wifiOnly} />
    {!wifiOnly ? <><Link className="button" href="/claim">我已有绑定码</Link><Link className="button secondary" href="/console/devices">设备已绑定，进入控制台</Link></> : null}
  </section></div></main>;
}
