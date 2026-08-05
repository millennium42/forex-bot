import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  /* config options here */
  experimental: {
    allowedDevOrigins: ['127.0.0.1', 'localhost'],
  },
  // Proxy para a API. 8001, não 8000: a 8000 é do OpenHands nesta máquina.
  // 127.0.0.1 e não localhost — no Docker Desktop do Windows, localhost resolve
  // para ::1 e a conexão fica pendurada sem timeout.
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: `${process.env.API_URL ?? 'http://127.0.0.1:8001'}/:path*`,
      },
    ];
  },
};

export default nextConfig;
