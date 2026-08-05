/** @type {import('next').NextConfig} */
// Design custom dirigido por paletas em TODA a app (ADR-013); o Splunk Design System e
// styled-components foram removidos na F-015 — sem `compiler.styledComponents`.
// F-041 (ADR-024 › separado): o navegador fala DIRETO com a API (subdomínio próprio, base injetado
// em runtime — ver lib/api.ts + app/layout.tsx). Sem rewrite/proxy aqui de propósito: `rewrites()`
// é avaliado no `next build` (destino vai p/ o routes-manifest) → não dá p/ configurar em runtime.
// Dev: `npm run dev` usa webpack (--webpack) — Turbopack (default no Next 16) entra em panic HMR
// ("Next.js package not found") quando o `.next` fica corrompido (ex.: build com dev ativo).
const nextConfig = {
  reactStrictMode: true,
  async redirects() {
    return [
      { source: "/admin/use-cases", destination: "/use-cases", permanent: false },
      { source: "/behind-the-scenes", destination: "/use-cases", permanent: false },
    ];
  },
};
export default nextConfig;
