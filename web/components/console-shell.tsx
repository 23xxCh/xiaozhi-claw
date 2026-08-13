"use client";

import { BrainIcon } from "@phosphor-icons/react/dist/csr/Brain";
import { ChartBarIcon } from "@phosphor-icons/react/dist/csr/ChartBar";
import { CirclesFourIcon } from "@phosphor-icons/react/dist/csr/CirclesFour";
import { HouseIcon } from "@phosphor-icons/react/dist/csr/House";
import { RobotIcon } from "@phosphor-icons/react/dist/csr/Robot";
import { SlidersHorizontalIcon } from "@phosphor-icons/react/dist/csr/SlidersHorizontal";
import { UserCircleIcon } from "@phosphor-icons/react/dist/csr/UserCircle";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { BrandFace } from "@/components/brand-face";
import { ThemeToggle } from "@/components/theme-toggle";

const links = [
  { label: "首页", href: "/console", icon: HouseIcon },
  { label: "设备", href: "/console/devices", icon: RobotIcon },
  { label: "助手", href: "/console/agents", icon: BrainIcon },
  { label: "记忆", href: "/console/memories", icon: CirclesFourIcon },
  { label: "用量", href: "/console/usage", icon: ChartBarIcon },
  { label: "账户", href: "/console/account", icon: UserCircleIcon },
];

function active(pathname: string, href: string) {
  return href === "/console" ? pathname === href : pathname.startsWith(href);
}

export function ConsoleShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [moreOpen, setMoreOpen] = useState(false);
  const moreRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function close(event: MouseEvent) {
      if (!moreRef.current?.contains(event.target as Node)) setMoreOpen(false);
    }
    document.addEventListener("pointerdown", close);
    return () => document.removeEventListener("pointerdown", close);
  }, []);

  const desktopLinks = links;
  const mobileLinks = links.slice(0, 3);
  const moreLinks = [...links.slice(3), { label: "家庭管理", href: "/console/family", icon: UserCircleIcon }];
  const moreActive = moreLinks.some((item) => active(pathname, item.href));

  return (
    <div className="shell">
      <aside className="sidebar">
        <Link className="brand" href="/console" aria-label="Hensun AI 首页">
          <BrandFace small />
          <span className="brand-copy">Hensun AI<small>Personal console</small></span>
        </Link>
        <nav className="nav" aria-label="客户控制台">
          {desktopLinks.map((item) => {
            const Icon = item.icon;
            return (
              <Link className="nav-link" href={item.href} key={item.href} aria-current={active(pathname, item.href) ? "page" : undefined}>
                <Icon size={21} weight={active(pathname, item.href) ? "fill" : "regular"} />
                {item.label}
              </Link>
            );
          })}
        </nav>
        <div className="sidebar-foot">
          <ThemeToggle inverse />
          <small>当前公开版本仅供 18 岁以上用户使用</small>
        </div>
      </aside>
      <main className="main"><div className="content">{children}</div></main>
      <nav className="mobile-nav" aria-label="手机导航" ref={moreRef}>
        {mobileLinks.map((item) => {
          const Icon = item.icon;
          return (
            <Link href={item.href} key={item.href} aria-current={active(pathname, item.href) ? "page" : undefined}>
              <Icon size={22} weight={active(pathname, item.href) ? "fill" : "regular"} />
              {item.label}
            </Link>
          );
        })}
        <button type="button" aria-expanded={moreOpen} aria-label="打开更多导航" onClick={() => setMoreOpen((value) => !value)}>
          <SlidersHorizontalIcon size={22} weight={moreActive ? "fill" : "regular"} />更多
        </button>
        {moreOpen ? (
          <div className="mobile-more">
            {moreLinks.map((item) => {
              const Icon = item.icon;
              return <Link href={item.href} key={item.href} onClick={() => setMoreOpen(false)}><Icon size={20} />{item.label}</Link>;
            })}
          </div>
        ) : null}
      </nav>
    </div>
  );
}
