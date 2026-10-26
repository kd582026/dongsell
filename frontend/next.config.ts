import type { NextConfig } from 'next';
const config: NextConfig = {
  env: {NEXT_PUBLIC_UI_PREVIEW: process.env.VERCEL === '1' ? 'true' : (process.env.NEXT_PUBLIC_UI_PREVIEW || 'false')},
  async rewrites() {
    if (process.env.VERCEL === '1' || process.env.NEXT_PUBLIC_UI_PREVIEW === 'true') return [];
    return [{source: '/api/:path*', destination: 'http://127.0.0.1:8000/api/:path*'},
            {source: '/images/:path*', destination: 'http://127.0.0.1:8000/images/:path*'}];
  },
};
export default config;
