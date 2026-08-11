/** Version baked in CI (ADR-036) — NEXT_PUBLIC_* inlined in the next build. */
export function vegaVersionLabel(): string {
  const v = process.env.NEXT_PUBLIC_VEGA_VERSION || "dev";
  const sha = process.env.NEXT_PUBLIC_VEGA_GIT_SHA;
  return sha && sha !== "local" ? `${v} (${sha})` : v;
}
