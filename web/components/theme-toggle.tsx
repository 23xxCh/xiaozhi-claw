"use client";

import { MoonIcon } from "@phosphor-icons/react/dist/csr/Moon";
import { SunIcon } from "@phosphor-icons/react/dist/csr/Sun";

export function ThemeToggle({ inverse = false }: { inverse?: boolean }) {
  function toggle() {
    const stored = document.cookie.match(/(?:^|; )hensun_theme=(light|dark)/)?.[1];
    const current = stored ?? (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    const next = current === "light" ? "dark" : "light";
    document.documentElement.dataset.theme = next;
    document.cookie = `hensun_theme=${next}; Path=/; Max-Age=31536000; SameSite=Lax`;
  }

  return (
    <button className={`button icon-button ${inverse ? "ghost" : "secondary"}`} type="button" aria-label="切换浅色或深色模式" title="切换浅色或深色模式" onClick={toggle}>
      <MoonIcon className="theme-light-icon" size={20} />
      <SunIcon className="theme-dark-icon" size={20} />
    </button>
  );
}
