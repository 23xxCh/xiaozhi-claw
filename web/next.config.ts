import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  // Playwright starts the development server on localhost while tests use
  // 127.0.0.1. Keep that local-only origin explicit so development and CI
  // exercise the same browser flow instead of failing on Next's dev guard.
  allowedDevOrigins: ["127.0.0.1", "localhost"],
};

export default nextConfig;
