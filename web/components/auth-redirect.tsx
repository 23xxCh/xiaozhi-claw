"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

export function AuthRedirect() {
  const router = useRouter();
  useEffect(() => {
    function navigate(event: Event) {
      router.replace((event as CustomEvent<string>).detail);
    }
    window.addEventListener("hensun-auth-redirect", navigate);
    return () => window.removeEventListener("hensun-auth-redirect", navigate);
  }, [router]);
  return null;
}
