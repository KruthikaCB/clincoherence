/** @type {import('next').NextConfig} */
const nextConfig = {
  async rewrites() {
    const origin = process.env.BACKEND_ORIGIN || "http://127.0.0.1:8000";
    return [{ source: "/api/v1/:path*", destination: `${origin}/api/v1/:path*` }];
  },
};
export default nextConfig;
