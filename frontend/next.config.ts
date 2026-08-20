import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Emits .next/standalone: a self-contained server bundle with only the
  // dependencies actually reached, so the runtime image does not need
  // node_modules or the sources. See frontend/Dockerfile.
  output: "standalone",
};

export default nextConfig;
