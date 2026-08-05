import "./globals.css";
import { headers } from "next/headers";
import { Manrope } from "next/font/google";
import Providers from "@/lib/providers";

export const metadata = { title: "Vega", description: "Your AI shopping concierge" };

// Tipografia da LOJA: Manrope via next/font (self-hosted, sem FOUT/erro de rede na VM
// offline). Exposta como variável CSS e usada por .ns-store (ver globals.css). ADR-012.
const manrope = Manrope({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700", "800"],
  variable: "--font-manrope",
  display: "swap",
});

// Define paleta + esquema no <html> ANTES do paint, a partir da preferência salva —
// evita flash do canvas/cores em quem mudou o tema. O React (PaletteProvider) assume a
// sincronia após hidratar. Defaults: Splunk / light. Ver lib/theme-context e ADR-012.
const THEME_INIT = `(function(){try{var r=document.documentElement;var s=localStorage.getItem('vega.colorScheme');r.dataset.scheme=(s==='dark'||s==='light')?s:'light';var p=localStorage.getItem('vega.palette');r.dataset.palette=(p==='splunk'||p==='blue'||p==='sunset'||p==='mono')?p:'splunk';}catch(e){var r2=document.documentElement;r2.dataset.scheme='light';r2.dataset.palette='splunk';}})();`;

// Splunk RUM (F-040-RUM): server-render do snippet bruto que o owner colou. Buscamos a config
// no backend NO REQUEST (no-store → ligar/desligar reflete na hora); falha → não injeta nada
// (standalone-first, nunca quebra a página). O snippet vira um bootstrap inline que reconstrói
// seus <script> como elementos DOM reais com async=false (preserva a ordem: agente externo
// carrega ANTES do SplunkRum.init). `<` é escapado (<) p/ o snippet não fechar este <script>.
// Server component (SSR) → fala com o backend interno direto (F-041/ADR-024): API_INTERNAL_URL
// em runtime (default dev = localhost:8000; no compose = http://backend:8000).
const API_BASE = process.env.API_INTERNAL_URL || "http://localhost:8000";

// Base PÚBLICO da API p/ o NAVEGADOR (separado, ADR-024). Lido AQUI no request (server-render) e
// injetado como `window.__API_BASE` ANTES de qualquer código do cliente — assim NÃO é "baked" no
// build (mantém 1 imagem servindo qualquer host). `lib/api.ts` lê `window.__API_BASE` no browser.
// Resolução (F-047, ADR-025 — portas diretas, sem Traefik):
//   1) PUBLIC_API_BASE explícito no .env (se o owner quiser fixar) vence;
//   2) senão, deriva do HOST do request → http://<host-sem-porta>:8000 (o participante acessa a VM
//      por IP:3000 e a API vive em IP:8000 na MESMA VM — sem precisar saber o IP no build);
//   3) fallback dev = http://localhost:8000.
async function resolvePublicApiBase(): Promise<string> {
  const explicit = process.env.PUBLIC_API_BASE;
  if (explicit) return explicit;
  const host = (await headers()).get("host") || "";
  const hostname = host.split(":")[0];
  if (hostname) return `http://${hostname}:8000`;
  return "http://localhost:8000";
}

async function rumInjectScript(): Promise<string | null> {
  let snippet = "";
  try {
    const r = await fetch(`${API_BASE}/api/rum`, { cache: "no-store" });
    if (r.ok) snippet = ((await r.json())?.snippet as string) || "";
  } catch {
    return null; // backend fora do ar / sem rede → segue sem RUM
  }
  if (!snippet.trim()) return null;
  // O bootstrap reconstrói os <script> do snippet como elementos DOM reais com `async=false`
  // (executa na ORDEM de inserção, cada um após carregar). PORÉM: um <script> INLINE inserido
  // dinamicamente roda na hora do append — antes do agente externo carregar → `SplunkRum.init`
  // dispararia com `SplunkRum` indefinido. Por isso convertemos cada inline num **blob: URL**
  // (vira "externo"), então ele entra na MESMA fila ordenada: agente externo → init(blob) →
  // recorder → recorder.init(blob). Notas de sintaxe: o `<` do snippet é escapado p/ não fechar
  // este <script>; o bootstrap evita o operador `<` (React troca `<`→< em conteúdo de script).
  const data = JSON.stringify(snippet).replace(/</g, "\\u003c");
  return `(function(){try{var html=${data};var tmp=document.createElement('div');tmp.innerHTML=html;[].forEach.call(tmp.querySelectorAll('script'),function(old){var s=document.createElement('script');[].forEach.call(old.attributes,function(a){s.setAttribute(a.name,a.value);});s.async=false;if(!old.src){var blob=new Blob([old.textContent||''],{type:'text/javascript'});s.src=URL.createObjectURL(blob);}document.head.appendChild(s);});}catch(e){}})();`;
}

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const rumInject = await rumInjectScript();
  const apiBaseInit = `window.__API_BASE=${JSON.stringify(await resolvePublicApiBase())};`;
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: apiBaseInit }} />
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT }} />
        {rumInject && <script dangerouslySetInnerHTML={{ __html: rumInject }} />}
      </head>
      <body className={manrope.variable}>
        {/* Tema = paleta + light/dark (ADR-012). Loja: design custom por variáveis CSS;
            telas técnicas (Behind the Scenes): @splunk/react-ui pelo esquema do tema. */}
        <Providers>
          {children}
        </Providers>
      </body>
    </html>
  );
}
