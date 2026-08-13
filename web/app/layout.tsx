import type { Metadata } from "next";
import localFont from "next/font/local";
import { cookies } from "next/headers";

import { AppTheme } from "@/components/app-theme";
import { AuthRedirect } from "@/components/auth-redirect";

import "./globals.css";

const geistMono = localFont({
  src: "../node_modules/geist/dist/fonts/geist-mono/GeistMono-Regular.woff2",
  variable: "--font-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: { default: "Hensun AI", template: "%s · Hensun AI" },
  description: "管理 Hensun 桌面 AI 设备、助手与隐私设置",
};

export default async function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  const themeCookie = (await cookies()).get("hensun_theme")?.value;
  const theme = themeCookie === "light" || themeCookie === "dark" ? themeCookie : undefined;
  return (
    <html lang="zh-CN" data-theme={theme} suppressHydrationWarning>
      <body className={geistMono.variable}>
        <AppTheme><AuthRedirect />{children}</AppTheme>
      </body>
    </html>
  );
}
