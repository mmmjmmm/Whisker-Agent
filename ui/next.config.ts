import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  images: {
    remotePatterns: [
      {
        protocol: "https",
        hostname: "**.aliyuncs.com",
      },
      {
        protocol: "https",
        hostname: "**.alicdn.com",
      },
    ],
  },
};

export default nextConfig;
