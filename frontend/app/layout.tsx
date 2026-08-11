import "./globals.css";
import { headers } from "next/headers";
import { Manrope } from "next/font/google";
import Providers from "@/lib/providers";

export const metadata = { title: "Vega", description: "Your AI shopping concierge" };

// STORE typography: Manrope via next/font (self-hosted, no FOUT/network error on the
// offline VM). Exposed as a CSS variable and used by .ns-store (see globals.css). ADR-012.
const manrope = Manrope({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700", "800"],
  variable: "--font-manrope",
  display: "swap",
});

// Sets palette + scheme on the <html> BEFORE paint, from the saved preference —
// avoids a canvas/color flash for anyone who changed the theme. React (PaletteProvider)
// takes over the sync after hydration. Defaults: Splunk / light. See lib/theme-context and ADR-012.
const THEME_INIT = `(function(){try{var r=document.documentElement;var s=localStorage.getItem('vega.colorScheme');r.dataset.scheme=(s==='dark'||s==='light')?s:'light';var p=localStorage.getItem('vega.palette');r.dataset.palette=(p==='splunk'||p==='blue'||p==='sunset'||p==='mono')?p:'splunk';}catch(e){var r2=document.documentElement;r2.dataset.scheme='light';r2.dataset.palette='splunk';}})();`;

// Splunk RUM (F-040-RUM): server-render of the raw snippet the owner pasted. We fetch the config
// from the backend ON EACH REQUEST (no-store → toggling on/off reflects immediately); failure →
// injects nothing (standalone-first, never breaks the page). The snippet becomes an inline
// bootstrap that rebuilds its <script> tags as real DOM elements with async=false (preserves the
// order: external agent loads BEFORE SplunkRum.init). `<` is escaped (<) so the snippet doesn't
// close this <script>. Server component (SSR) → talks to the internal backend directly
// (F-041/ADR-024): API_INTERNAL_URL at runtime (dev default = localhost:8000; in compose =
// http://backend:8000).
const API_BASE = process.env.API_INTERNAL_URL || "http://localhost:8000";

// PUBLIC API base for the BROWSER (separate, ADR-024). Read HERE on the request (server-render) and
// injected as `window.__API_BASE` BEFORE any client code — so it is NOT "baked" into the
// build (keeps 1 image serving any host). `lib/api.ts` reads `window.__API_BASE` in the browser.
// Resolution (F-047, ADR-025 — direct ports, no Traefik):
//   1) explicit PUBLIC_API_BASE in .env (if the owner wants to pin it) wins;
//   2) otherwise, derives from the request's HOST → http://<host-without-port>:8000 (the participant
//      accesses the VM by IP:3000 and the API lives on IP:8000 on the SAME VM — no need to know the
//      IP at build time);
//   3) dev fallback = http://localhost:8000.
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
    return null; // backend unreachable / no network → carries on without RUM
  }
  if (!snippet.trim()) return null;
  // The bootstrap rebuilds the snippet's <script> tags as real DOM elements with `async=false`
  // (executes in INSERTION order, each one after it loads). HOWEVER: a dynamically inserted
  // INLINE <script> runs at append time — before the external agent loads → `SplunkRum.init`
  // would fire with `SplunkRum` undefined. So we convert each inline script into a **blob: URL**
  // (making it "external"), so it enters the SAME ordered queue: external agent → init(blob) →
  // recorder → recorder.init(blob). Syntax notes: the snippet's `<` is escaped so it doesn't close
  // this <script>; the bootstrap avoids the `<` operator (React swaps `<`→< in script content).
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
        {/* Theme = palette + light/dark (ADR-012). Store: custom design via CSS variables;
            technical screens (Behind the Scenes): @splunk/react-ui via the theme scheme. */}
        <Providers>
          {children}
        </Providers>
      </body>
    </html>
  );
}
