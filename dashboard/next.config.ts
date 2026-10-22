import type { NextConfig } from "next";

/* The deployed dashboard reaches the API through its own origin: /zapi/* on the Vercel
   domain is proxied to the backend on Catalyst AppSail — its Development environment,
   which every push to main deploys (.github/workflows/snapshot.yml). Same origin means no
   CORS to configure, a waking backend's gateway error arrives as a status the page can
   retry rather than an opaque network failure, and a stale NEXT_PUBLIC_API_BASE in the
   hosting project can no longer point the page at the wrong backend.

   ZONULY_API_ORIGIN (server-side, read at build) overrides the target. Locally, outside
   Vercel, nothing changes: the page talks to NEXT_PUBLIC_API_BASE or 127.0.0.1:8000. */
const API_ORIGIN = (process.env.ZONULY_API_ORIGIN ?? "https://zonuly-api-50046893527.development.catalystappsail.in").replace(/\/+$/, "");
const ON_VERCEL = process.env.VERCEL === "1";

const nextConfig: NextConfig = {
  env: {
    NEXT_PUBLIC_API_PROXY: ON_VERCEL ? "/zapi" : "",
  },
  async rewrites() {
    return [{ source: "/zapi/:path*", destination: `${API_ORIGIN}/:path*` }];
  },
  /* Let Vercel's edge keep what the backend marks cacheable. The public API stamps its
     snapshot reads `s-maxage=600, stale-while-revalidate=604800` (jobhunter/snapshot_cache.py),
     but a project created before April 2026 ignores cache headers on an external rewrite
     unless told otherwise — every /zapi read went to AppSail (x-vercel-cache: MISS), and
     AppSail's Development instance sleeps a minute after its last request, so most visitors
     paid a cold start. With this, a visit after the first is answered at the edge, stale
     while the backend wakes behind it. Live probes (/api/health) carry no cache
     header upstream and stay uncached. */
  async headers() {
    return [{ source: "/zapi/:path*", headers: [{ key: "x-vercel-enable-rewrite-caching", value: "1" }] }];
  },
};

export default nextConfig;
