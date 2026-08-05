// Helpers da Loja (vitrine). Texto de UI em inglês; aqui só lógica/derivações.
import { Product, Tier } from "./api";

export const CATEGORIES = ["All", "Audio", "Wearables", "Home", "Gifts"] as const;
export type Category = (typeof CATEGORIES)[number];

export type CartItem = { product: Product; qty: number };

// Tags do catálogo (backend) → categorias exibidas (inglês).
const TAG_TO_CATEGORY: Record<string, Category> = {
  audio: "Audio",
  wearable: "Wearables",
  casa: "Home",
  presente: "Gifts",
};

const CATEGORY_EMOJI: Record<Category, string> = {
  All: "🛍️",
  Audio: "🎧",
  Wearables: "⌚",
  Home: "🏠",
  Gifts: "🎁",
};

export function categoriesOf(p: Product): Category[] {
  return p.tags.map((t) => TAG_TO_CATEGORY[t]).filter(Boolean) as Category[];
}

export function inCategory(p: Product, c: Category): boolean {
  return c === "All" || categoriesOf(p).includes(c);
}

// Placeholder de imagem: emoji por SKU (sem assets externos; VM offline). Cada produto tem
// ícone próprio — evita vitrine monótona quando vários itens compartilham categoria.
const SKU_EMOJI: Record<string, string> = {
  "NS-001": "🎧", // Aura Bluetooth Headphones
  "NS-002": "⌚", // Smartwatch Pulse
  "NS-003": "🔊", // Mini Bluetooth Speaker
  "NS-004": "☕", // Gourmet Coffee Kit
  "NS-005": "🎙️", // Headphone Studio Pro
  "NS-006": "🏃", // Earbuds Sport Lite
  "NS-007": "📺", // Soundbar Cinema 380
  "NS-008": "📊", // Fitness Band Move
  "NS-009": "⏱️", // Smartwatch Pulse Max
  "NS-010": "💍", // Halo Smart Ring
  "NS-011": "🌿", // Aroma Zen Humidifier
  "NS-012": "💡", // Smart Lumen Lamp
  "NS-013": "🫖", // Compact Brew Coffee Maker
  "NS-014": "🥤", // Nova Thermal Bottle
  "NS-015": "🎮", // Gaming Headset Pro
  "NS-016": "🎶", // Wireless Earbuds Pro
  "NS-017": "💿", // Vinyl Turntable Mini
  "NS-018": "🛡️", // Kids GPS Watch
  "NS-019": "🏔️", // Enduro Running Watch
  "NS-020": "😴", // Sleep Tracker Band
  "NS-021": "🌬️", // PureBreeze Air Purifier
  "NS-022": "🤖", // Robot Vacuum Lite
  "NS-023": "🍳", // Cast Iron Skillet Set
  "NS-024": "🫘", // Barista Espresso Machine
  "NS-025": "🕯️", // Scented Candle Gift Set
  "NS-026": "📽️", // Beam Portable Projector
  "NS-027": "🔇", // Earbuds Max ANC
  "NS-028": "⚖️", // Wellness Smart Scale
};

export function emojiOf(p: Product): string {
  const mapped = SKU_EMOJI[p.sku];
  if (mapped) return mapped;
  const primary = p.tags.find((t) => t !== "presente");
  const cat = TAG_TO_CATEGORY[primary ?? "presente"] ?? "Gifts";
  return CATEGORY_EMOJI[cat];
}

// Gradiente do placeholder (sem assets reais — decisão F-009). Estável por SKU, sorteado
// de uma paleta fixa de gradientes para a vitrine não ficar monótona. Ver ADR-012.
const PLACEHOLDER_GRADIENTS = [
  "linear-gradient(135deg,#6d5efc,#9c6bff 55%,#16c0a6)",
  "linear-gradient(135deg,#16c0a6,#0264d7)",
  "linear-gradient(135deg,#ff6b4a,#ffb020)",
  "linear-gradient(135deg,#9c6bff,#6d5efc)",
  "linear-gradient(135deg,#0264d7,#3993ff)",
  "linear-gradient(135deg,#f5576c,#f093fb)",
  "linear-gradient(135deg,#11998e,#38ef7d)",
  "linear-gradient(135deg,#fc4a1a,#f7b733)",
  "linear-gradient(135deg,#4776e6,#8e54e9)",
  "linear-gradient(135deg,#ee0979,#ff6a00)",
  "linear-gradient(135deg,#2193b0,#6dd5ed)",
  "linear-gradient(135deg,#834d9b,#d04ed6)",
  "linear-gradient(135deg,#355c7d,#6c5b7b,#c06c84)",
  "linear-gradient(135deg,#0f2027,#203a43,#2c5364)",
];

export function gradientOf(sku: string): string {
  let h = 0;
  for (const ch of sku) h = (h * 31 + ch.charCodeAt(0)) % 997;
  return PLACEHOLDER_GRADIENTS[h % PLACEHOLDER_GRADIENTS.length];
}


export function formatMoney(v: number): string {
  return v.toLocaleString("en-US", { style: "currency", currency: "USD" });
}

// Rating cosmético e estável (4.2–4.9) derivado do SKU — o catálogo mock não traz rating.
export function ratingOf(sku: string): number {
  let h = 0;
  for (const ch of sku) h = (h * 31 + ch.charCodeAt(0)) % 1000;
  return Math.round((4.2 + (h % 8) / 10) * 10) / 10;
}

// Tiers (F-008): thresholds de gasto acumulado (USD). ESPELHAM os defaults do backend
// (users.py › TIER_GOLD_USD/TIER_PLATINUM_USD); usados só para exibir o progresso ao
// próximo tier. O tier de verdade é sempre o computado pelo backend.
export const TIER_GOLD_AT = 1000;
export const TIER_PLATINUM_AT = 5000;

// Mensagem de progresso ao próximo tier (ou null se já é o topo).
export function nextTierHint(tier: Tier, spend: number): string | null {
  if (tier === "PLATINUM") return null;
  const target = tier === "GOLD" ? TIER_PLATINUM_AT : TIER_GOLD_AT;
  const next = tier === "GOLD" ? "Platinum" : "Gold";
  const remaining = Math.max(0, target - spend);
  return `Spend ${formatMoney(remaining)} more to reach ${next}`;
}

// Estoque (F-005): backend baixa `stock` ao fechar o pedido. Espelha LOW_STOCK_THRESHOLD do backend.
export const LOW_STOCK = 3;
export type StockState = "in" | "low" | "out";

export function stockState(p: Product): StockState {
  if (p.stock === undefined) return "in"; // catálogo sem stock (compat) → tratar como disponível
  if (p.stock <= 0) return "out";
  if (p.stock <= LOW_STOCK) return "low";
  return "in";
}
