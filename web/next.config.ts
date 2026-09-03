import type { NextConfig } from "next";

const controlApiProxyUrl = process.env.CONTROL_API_PROXY_URL?.replace(/\/$/, "");

const nextConfig: NextConfig = {
  output: "standalone",
  // Playwright starts the development server on localhost while tests use
  // 127.0.0.1. Keep that local-only origin explicit so development and CI
  // exercise the same browser flow instead of failing on Next's dev guard.
  allowedDevOrigins: ["127.0.0.1", "localhost"],
  async rewrites() {
    if (!controlApiProxyUrl) {
      return [];
    }
    return [{ source: "/v1/:path*", destination: `${controlApiProxyUrl}/v1/:path*` }];
  },
};

export default nextConfig;
