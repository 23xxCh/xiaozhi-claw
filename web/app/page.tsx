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
          <Link className="action" href="/login">邮箱登录并开始</Link>
        </div>
        <div className="hero-card">
          <BrandFace />
          <h2>第一次使用只做三件事</h2>
          <ul>
            <li>设备进入配网热点，写入家庭 Wi‑Fi</li>
            <li>登录后输入设备显示的 6 位绑定码</li>
            <li>选择角色和音色，下一轮对话自动生效</li>
          </ul>
          <p className="muted">当前公开版本仅供 18 岁以上成年人使用。你正在与 AI 而非真人互动。</p>
        </div>
      </section>
    </main>
  );
}
