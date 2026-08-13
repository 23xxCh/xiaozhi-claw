import { notFound } from "next/navigation";

import { BrandFace } from "@/components/brand-face";
import { Loading, SectionError } from "@/components/page-state";
import { ThemeToggle } from "@/components/theme-toggle";

export default function DesignPreviewPage() {
  if (process.env.NEXT_PUBLIC_ENABLE_DEV_LOGIN !== "true") notFound();
  return (
    <main className="main">
      <div className="content stack">
        <header className="page-head"><div><div className="eyebrow">Development only</div><h1>Hensun 设计预览</h1><p>真实组件状态；确认视觉后再随业务页面一起迭代。</p></div><ThemeToggle /></header>
        <section className="card hero-panel split"><div className="stack"><span className="status online">设备可以聊天</span><h2>年轻全龄鸭黄，不做幼儿化</h2><p className="muted" style={{ margin: 0 }}>温暖、清楚、有辨识度，同时保持成人用户需要的可信感。</p><div className="row"><button className="button">主要操作</button><button className="button secondary">次要操作</button><button className="button danger">谨慎操作</button></div></div><BrandFace /></section>
        <div className="grid"><section className="card"><div className="muted">设备状态</div><div className="metric">1/1</div><span className="status online">在线</span></section><section className="card"><div className="muted">当前助手</div><div className="metric" style={{ fontSize: "1.8rem" }}>小恒</div><span className="status">温和可靠</span></section><section className="card"><div className="muted">本月对话</div><div className="metric">128</div><span className="status warning">免费内测</span></section></div>
        <div className="grid two"><section className="card stack"><h2>表单</h2><div className="field"><label htmlFor="preview-name">名称</label><input id="preview-name" defaultValue="小恒" /></div><div className="field"><label htmlFor="preview-voice">声音</label><select id="preview-voice" defaultValue="cherry"><option value="cherry">Cherry / 温暖女声</option></select></div><label className="check"><input type="checkbox" defaultChecked />允许保存加密摘要记忆</label></section><section className="card stack"><h2>完整状态</h2><div className="success">设置已保存，下轮对话生效。</div><div className="warning">设备离线，仍显示上次成功数据。</div><div className="error">这一部分暂时无法更新。</div></section></div>
        <section><h2 style={{ marginBottom: 14 }}>同形骨架</h2><Loading /></section>
        <SectionError message="网络暂时不可用" updatedAt={new Date("2026-08-13T12:00:00+08:00")} />
      </div>
    </main>
  );
}
