"use client";
// Chat flutuante global — estado, sessionStorage e openChat() p/ deep links (F-051).
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { ChatMessage, ChatResult, sendChatMessage } from "@/lib/api";

const STORAGE_KEY = "vega.chat.thread";

export type ChatTurn = ChatMessage & { result?: ChatResult };

type OpenChatOpts = {
  seed?: string;
  sku?: string;
  orderId?: string;
};

type ChatContextValue = {
  open: boolean;
  turns: ChatTurn[];
  loading: boolean;
  contextSku: string;
  contextOrderId: string;
  input: string;
  setInput: (v: string) => void;
  setContextSku: (v: string) => void;
  clearContextSku: (sku?: string) => void;
  clearContextOrderId: (orderId?: string) => void;
  openChat: (opts?: OpenChatOpts) => void;
  closeChat: () => void;
  toggleChat: () => void;
  clearSession: () => void;
  send: (text?: string) => Promise<void>;
};

const ChatContext = createContext<ChatContextValue | null>(null);

function loadTurns(): ChatTurn[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as ChatTurn[]) : [];
  } catch {
    return [];
  }
}

function saveTurns(turns: ChatTurn[]) {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(turns));
  } catch {
    /* quota exceeded */
  }
}

export function ChatProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [loading, setLoading] = useState(false);
  const [contextSku, setContextSku] = useState("");
  const [contextOrderId, setContextOrderId] = useState("");
  const [input, setInput] = useState("");
  const pendingSeed = useRef<string | null>(null);
  const hydrated = useRef(false);

  useEffect(() => {
    if (!hydrated.current) {
      hydrated.current = true;
      setTurns(loadTurns());
    }
  }, []);

  useEffect(() => {
    if (hydrated.current) saveTurns(turns);
  }, [turns]);

  const send = useCallback(
    async (text?: string) => {
      const msg = (text ?? input).trim();
      if (!msg || loading) return;

      const userTurn: ChatTurn = { role: "user", content: msg };
      const history: ChatMessage[] = [
        ...turns.map(({ role, content }) => ({ role, content })),
        userTurn,
      ];
      setTurns((prev) => [...prev, userTurn]);
      setInput("");
      setLoading(true);

      try {
        const ctx: { sku?: string; order_id?: string } = {};
        if (contextSku) ctx.sku = contextSku;
        if (contextOrderId) ctx.order_id = contextOrderId;
        const result = await sendChatMessage(history, Object.keys(ctx).length ? ctx : undefined);
        setTurns((prev) => [
          ...prev,
          { role: "assistant", content: result.reply || "Done.", result },
        ]);
      } catch {
        setTurns((prev) => [
          ...prev,
          { role: "assistant", content: "Something went wrong. Please try again." },
        ]);
      } finally {
        setLoading(false);
      }
    },
    [input, loading, turns, contextSku, contextOrderId],
  );

  const openChat = useCallback((opts?: OpenChatOpts) => {
    if (opts?.seed) {
      // Nova conversa — evita empilhar seed demo (ex. banda Concierge) no histórico persistido.
      setTurns([]);
      setContextSku("");
      setContextOrderId("");
      try {
        sessionStorage.removeItem(STORAGE_KEY);
      } catch {
        /* quota / private mode */
      }
      setInput(opts.seed);
      pendingSeed.current = opts.seed;
    }
    // seed e contexto podem vir juntos (ex. "Ask via chat" num pedido DELIVERED).
    if (opts?.sku) setContextSku(opts.sku);
    if (opts?.orderId) setContextOrderId(opts.orderId);
    setOpen(true);
  }, []);

  const closeChat = useCallback(() => setOpen(false), []);
  const toggleChat = useCallback(() => setOpen((o) => !o), []);

  const clearContextSku = useCallback((sku?: string) => {
    setContextSku((current) => (sku && current !== sku ? current : ""));
  }, []);

  const clearContextOrderId = useCallback((orderId?: string) => {
    setContextOrderId((current) => (orderId && current !== orderId ? current : ""));
  }, []);

  const clearSession = useCallback(() => {
    if (loading) return;
    pendingSeed.current = null;
    setTurns([]);
    setInput("");
    setContextSku("");
    setContextOrderId("");
    try {
      sessionStorage.removeItem(STORAGE_KEY);
    } catch {
      /* quota / private mode */
    }
  }, [loading]);

  useEffect(() => {
    if (open && pendingSeed.current) {
      const seed = pendingSeed.current;
      pendingSeed.current = null;
      send(seed);
    }
  }, [open, send]);

  const value = useMemo(
    () => ({
      open,
      turns,
      loading,
      contextSku,
      contextOrderId,
      input,
      setInput,
      setContextSku,
      clearContextSku,
      clearContextOrderId,
      openChat,
      closeChat,
      toggleChat,
      clearSession,
      send,
    }),
    [
      open,
      turns,
      loading,
      contextSku,
      contextOrderId,
      input,
      openChat,
      closeChat,
      toggleChat,
      clearContextSku,
      clearContextOrderId,
      clearSession,
      send,
    ],
  );

  return <ChatContext.Provider value={value}>{children}</ChatContext.Provider>;
}

export function useChat() {
  const ctx = useContext(ChatContext);
  if (!ctx) throw new Error("useChat must be used within ChatProvider");
  return ctx;
}

/** Solta sku/orderId do chat quando o componente-página desmonta (ex. sair do PDP ou do detalhe). */
export type ChatPageScope = { sku?: string; orderId?: string };

export function useChatPageScope(scope: ChatPageScope) {
  const { clearContextSku, clearContextOrderId } = useChat();
  const { sku, orderId } = scope;

  useEffect(() => {
    return () => {
      if (sku) clearContextSku(sku);
      if (orderId) clearContextOrderId(orderId);
    };
  }, [sku, orderId, clearContextSku, clearContextOrderId]);
}
