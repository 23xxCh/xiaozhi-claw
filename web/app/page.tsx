import Link from "next/link";
import { BrandFace } from "@/components/brand-face";

export default function Home() {
  return (
    <main className="landing">
      <section className="hero">
        <div className="hero-copy">
          <div className="eyebrow">Hensun Desk</div>
          <h1>你的桌面 AI，按你的方式说话。</h1>
          <p className="lead">
            配置角色、音色和快速对话预设，管理设备、摘要记忆与实际用量。摄像头云端能力当前关闭。
          </p>
          <Link className="action" href="/setup">开始使用</Link>
          <Link className="button secondary" href="/console">已有设备，进入控制台</Link>
        </div>
        <div className="hero-card">
          <BrandFace />
          <h2>第一次使用只做三件事</h2>
          <ul>
            <li>设备进入配网热点，写入家庭 Wi‑Fi</li>
            <li>扫描屏幕二维码，正常登录并绑定设备</li>
            <li>确认在线后开始聊天；角色和音色以后再改</li>
          </ul>
          <p className="muted">当前公开版本仅供 18 岁以上成年人使用。你正在与 AI 而非真人互动。</p>
        </div>
      </section>
    </main>
  );
}
