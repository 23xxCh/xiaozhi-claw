"use client";

import { Theme } from "@radix-ui/themes";

export function AppTheme({ children }: { children: React.ReactNode }) {
  return (
    <Theme accentColor="yellow" grayColor="sand" radius="large" scaling="100%">
      {children}
    </Theme>
  );
}
