import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Hensun AI 控制台",
  description: "Hensun 成人桌面 AI 设备与智能体控制台",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
