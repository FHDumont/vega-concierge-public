"use client";
// AGENTS — VISUAL editor for the orchestration (owner-only; F-027 / F-050 ADR-029). Shows the
// real graph: concierge hub-and-spoke (coordinator with no tools → curator/respond → back to
// coordinator), the other ReAct flows (fulfillment/compare/returns), and standalone features (F-022).
import { useCallback, useEffect, useMemo, useState } from "react";
import { useAuth } from "@/lib/auth";
import AgentCard from "@/components/AgentCard";
import {
  AgentTopology, TopologyCluster, TopologyNode, AgentConfig, LLMProvider,
  getAgentTopology, getAgents, getProviders,
} from "@/lib/api";

export default function AgentsPage() {
  const { user, ready } = useAuth();
  if (!ready) return <Gate msg="Loading…" />;
  if (!user) return <Gate msg="Sign in as the owner to edit the agents." cta />;
  if (user.role !== "OWNER") return <Gate msg="Owner only — you don’t have access to the agent editor." />;
  return <AgentsEditor />;
}

function Gate({ msg, cta }: { msg: string; cta?: boolean }) {
  return (
    <div className="ns-adm-wrap">
      <div className="ns-adm-top">
        <div>
          <h1>Agents</h1>
          <p className="sub">Visual orchestration editor — owner only.</p>
        </div>
      </div>
      <div className="ns-adm-card">
        <p className="ns-adm-empty">{msg}</p>
        {cta && <p style={{ marginTop: 10 }}><a className="ns-adm-btn primary" href="/account">Go to sign in</a></p>}
      </div>
    </div>
  );
}

function AgentsEditor() {
  const [topo, setTopo] = useState<AgentTopology | null>(null);
  const [agents, setAgents] = useState<AgentConfig[]>([]);
  const [providers, setProviders] = useState<LLMProvider[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null); // selected agent (config open)

  const loadAgents = useCallback(async () => {
    try { setAgents(await getAgents()); } catch (e) { setError((e as Error).message); }
  }, []);
  useEffect(() => {
    Promise.all([getAgentTopology(), getProviders()])
      .then(([t, p]) => { setTopo(t); setProviders(p); })
      .catch((e) => setError((e as Error).message));
    loadAgents();
  }, [loadAgents]);

  const selectedAgent = useMemo(
    () => agents.find((a) => a.agent === selected) ?? null, [agents, selected]);

  if (error && !topo) {
    return <div className="ns-adm-wrap"><div className="ns-cfg-test err">{error}</div></div>;
  }
  if (!topo) return <div className="ns-adm-wrap"><div className="ns-adm-empty">Loading orchestration…</div></div>;

  return (
    <div className="ns-adm-wrap">
      <div className="ns-adm-top">
        <div>
          <h1>Agents</h1>
          <p className="sub">Visual map of the real orchestration graph (ADR-029). Concierge is hub-and-spoke: the coordinator routes only — specialists hold tools or compose the answer. Click an agent node to edit its config.</p>
        </div>
      </div>

      {error && <div className="ns-cfg-test err" style={{ marginBottom: 12 }}>{error}</div>}

      <div className="ns-agt-layout">
        <div className="ns-agt-canvas">
          <div className="ns-agt-section">
            <h3 className="ns-cfg-group">Orchestrated</h3>
            <p className="ns-adm-note">Orchestrated flows — concierge uses hub-and-spoke (coordinator ↔ specialists); checkout/compare/returns use ReAct loops (coordinator ↔ tools → finalize).</p>
            <div className="ns-agt-clusters">
              {topo.clusters.map((c) => (
                <ClusterDiagram key={c.id} cluster={c} selected={selected} onPick={setSelected} />
              ))}
            </div>
          </div>

          <div className="ns-agt-section">
            <h3 className="ns-cfg-group">Standalone features</h3>
            <p className="ns-adm-note">Direct-call store AI features — each runs on its own (no orchestration).</p>
            <div className="ns-agt-standalone">
              {topo.standalone.map((n) => (
                <NodeChip key={n.id} node={n} selected={selected === n.agent}
                  onPick={() => n.agent && setSelected(n.agent)} />
              ))}
            </div>
          </div>
        </div>

        <aside className="ns-agt-panel">
          {selectedAgent ? (
            <>
              <div className="ns-agt-panel-head">
                <h3 className="ns-cfg-group" style={{ margin: 0 }}>Edit agent</h3>
                <button type="button" className="ns-adm-btn" onClick={() => setSelected(null)}>Close</button>
              </div>
              <AgentCard key={selectedAgent.agent} agent={selectedAgent} providers={providers} onSaved={loadAgents} />
            </>
          ) : (
            <div className="ns-adm-card">
              <p className="ns-adm-empty">Click an agent node to edit its configuration.</p>
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}

// --- Diagram for a cluster (SVG) ---------------------------------------------
const NODE_W = 156;
const NODE_H = 46;
const COL_GAP = 56;
const ROW_GAP = 18;
const PAD = 14;

/** Fixed positions for hub-and-spoke — avoids a coordinator↔specialist cycle in the DAG layout. */
function layoutHubSpoke(cluster: TopologyCluster) {
  const byId = new Map(cluster.nodes.map((n) => [n.id, n]));
  const pos = new Map<string, { x: number; y: number }>();
  const col = (d: number, row: number) => ({ x: PAD + d * (NODE_W + COL_GAP), y: PAD + row * (NODE_H + ROW_GAP) });

  pos.set("concierge.workflow", col(0, 1));
  pos.set("concierge", col(1, 1));
  pos.set("curator", col(2, 0));
  pos.set("respond", col(2, 2));
  pos.set("tool.search_catalog", col(3, 0));
  pos.set("tool.get_price", col(3, 1));

  cluster.nodes.forEach((n) => {
    if (!pos.has(n.id)) pos.set(n.id, col(2, 3));
  });

  const xs = [...pos.values()].map((p) => p.x);
  const ys = [...pos.values()].map((p) => p.y);
  const width = PAD * 2 + Math.max(...xs) + NODE_W - PAD;
  const height = PAD * 2 + Math.max(...ys) + NODE_H - PAD;
  return { byId, pos, width, height };
}

function layoutDag(cluster: TopologyCluster) {
  const byId = new Map(cluster.nodes.map((n) => [n.id, n]));
  const depth = new Map<string, number>();
  const visiting = new Set<string>();

  const visit = (id: string, d: number) => {
    if ((depth.get(id) ?? -1) >= d) return;
    if (visiting.has(id)) return;
    visiting.add(id);
    depth.set(id, d);
    cluster.edges.filter((e) => e.from === id).forEach((e) => visit(e.to, d + 1));
    visiting.delete(id);
  };
  visit(cluster.root, 0);
  cluster.nodes.forEach((n) => { if (!depth.has(n.id)) depth.set(n.id, 0); });

  const cols: string[][] = [];
  cluster.nodes.forEach((n) => {
    const d = depth.get(n.id)!;
    (cols[d] ||= []).push(n.id);
  });

  const pos = new Map<string, { x: number; y: number }>();
  cols.forEach((ids, d) => {
    ids.forEach((id, i) => {
      pos.set(id, { x: PAD + d * (NODE_W + COL_GAP), y: PAD + i * (NODE_H + ROW_GAP) });
    });
  });
  const width = PAD * 2 + cols.length * NODE_W + Math.max(0, cols.length - 1) * COL_GAP;
  const rows = Math.max(1, ...cols.map((c) => c.length));
  const height = PAD * 2 + rows * NODE_H + (rows - 1) * ROW_GAP;
  return { byId, pos, width, height };
}

function layout(cluster: TopologyCluster) {
  if (cluster.id === "concierge") return layoutHubSpoke(cluster);
  return layoutDag(cluster);
}

function isReturnEdge(cluster: TopologyCluster, from: string, to: string): boolean {
  return cluster.id === "concierge" && (
    (from === "curator" && to === "concierge") || (from === "respond" && to === "concierge")
  );
}

function ClusterDiagram({ cluster, selected, onPick }: {
  cluster: TopologyCluster; selected: string | null; onPick: (agent: string) => void;
}) {
  const { byId, pos, width, height } = useMemo(() => layout(cluster), [cluster]);
  return (
    <div className="ns-agt-cluster">
      <div className="ns-agt-cluster-title">{cluster.label}</div>
      <div className="ns-agt-svg-wrap">
        <svg viewBox={`0 0 ${width} ${height}`} width={width} height={height} role="img"
          aria-label={`${cluster.label} orchestration diagram`}>
          {cluster.edges.map((e, i) => {
            const a = pos.get(e.from), b = pos.get(e.to);
            if (!a || !b) return null;
            const x1 = a.x + NODE_W, y1 = a.y + NODE_H / 2;
            const x2 = b.x, y2 = b.y + NODE_H / 2;
            const mx = (x1 + x2) / 2;
            const ret = isReturnEdge(cluster, e.from, e.to);
            const back = ret && b.x < a.x;
            const path = back
              ? `M ${x1} ${y1} C ${x1 + 40} ${y1 - 36}, ${x2 - 40} ${y2 - 36}, ${x2} ${y2}`
              : `M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}`;
            return (
              <path key={i} d={path} fill="none" stroke="var(--border)" strokeWidth={1.5}
                strokeDasharray={ret ? "5 4" : undefined} opacity={ret ? 0.75 : 1} />
            );
          })}
          {cluster.nodes.map((n) => {
            const p = pos.get(n.id)!;
            const clickable = n.kind === "agent";
            const sel = selected === n.agent && clickable;
            return (
              <g key={n.id} transform={`translate(${p.x} ${p.y})`}
                className={`ns-agt-node ${n.kind} ${clickable ? "clickable" : ""} ${sel ? "sel" : ""}`}
                onClick={clickable ? () => onPick(n.agent!) : undefined}
                role={clickable ? "button" : undefined} tabIndex={clickable ? 0 : undefined}
                onKeyDown={clickable ? (ev) => { if (ev.key === "Enter" || ev.key === " ") onPick(n.agent!); } : undefined}>
                <rect width={NODE_W} height={NODE_H} rx={9} />
                <text x={NODE_W / 2} y={n.role ? 19 : NODE_H / 2 + 1} className="lbl">{n.label}</text>
                {n.role && <text x={NODE_W / 2} y={33} className="role">{n.role}</text>}
                {n.kind !== "agent" && n.kind !== "workflow" && (
                  <text x={NODE_W - 8} y={14} className="tag">{n.kind}</text>
                )}
              </g>
            );
          })}
        </svg>
      </div>
    </div>
  );
}

// Standalone agent chip (no edges) — clickable to open the config.
function NodeChip({ node, selected, onPick }: { node: TopologyNode; selected: boolean; onPick: () => void }) {
  return (
    <button type="button" className={`ns-agt-chip ${selected ? "sel" : ""}`} onClick={onPick}>
      <span className="name">{node.label}</span>
      {node.role && <span className="role">{node.role}</span>}
    </button>
  );
}
