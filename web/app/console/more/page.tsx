import Link from "next/link";

export default function MorePage() {
  return <><header className="page-head"><div><div className="eyebrow">More</div><h1>更多</h1><p>隐私、用量、家庭管理和账户设置。</p></div></header><div className="stack"><Link className="card" href="/console/memories"><h2>记忆</h2><p className="muted">查看、导出和删除摘要记忆</p></Link><Link className="card" href="/console/usage"><h2>用量</h2><p className="muted">查看语音轮数与资源使用</p></Link><Link className="card" href="/console/family"><h2>家庭管理</h2><p className="muted">家庭模式仍处于内部验证</p></Link><Link className="card" href="/console/account"><h2>账户</h2><p className="muted">登录、主题和版本说明</p></Link></div></>;
}
