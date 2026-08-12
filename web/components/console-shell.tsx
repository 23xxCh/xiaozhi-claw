import Link from "next/link";

const links = [
  ["概览", "/console"],
  ["智能体", "/console/agents"],
  ["设备与绑定", "/console/devices"],
  ["摘要记忆", "/console/memories"],
  ["用量", "/console/usage"],
];

export function ConsoleShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">Hensun AI</div>
        <nav className="nav">
          {links.map(([label, href]) => (
            <Link href={href} key={href}>{label}</Link>
          ))}
          <Link href="/admin">内部后台</Link>
        </nav>
      </aside>
      <main className="main">{children}</main>
    </div>
  );
}
