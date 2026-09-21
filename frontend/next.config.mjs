/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',
  reactStrictMode: true,
  // Production (including Vercel `next build`) must use same-origin /api.
  // A localhost default here is inlined into the client bundle and breaks Microsoft OAuth.
  env: {
    NEXT_PUBLIC_API_BASE:
      process.env.NEXT_PUBLIC_API_BASE ||
      (process.env.NODE_ENV === "production" ? "/api" : "http://localhost:8000"),
  },
};
export default nextConfig;
