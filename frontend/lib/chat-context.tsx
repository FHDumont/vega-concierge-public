"use client";
// Global floating chat — state, sessionStorage, and openChat() for deep links (F-051).
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
import { ApiRateLimitError, ChatMessage, ChatResult, applyProblemPreset, sendChatMessage } from "@/lib/api";
import { shouldCloseChatForStoreNavigation } from "@/lib/chat-store-navigation";
import { useWorkshopProblems } from "@/lib/workshop-problems";

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
  const { problems } = useWorkshopProblems();
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
        // FLAGS are in-memory only — restart/reload zeros them while the UI can still show
        // Scenario ON (trace 56a930eb: problem_flags.prompt_injection=false at send time).
        if (problems.active_scenario) {
          await applyProblemPreset(problems.active_scenario);
        }
        const ctx: { sku?: string; order_id?: string } = {};
        if (contextSku) ctx.sku = contextSku;
        if (contextOrderId) ctx.order_id = contextOrderId;
        const result = await sendChatMessage(history, Object.keys(ctx).length ? ctx : undefined);
        setTurns((prev) => [
          ...prev,
          { role: "assistant", content: result.reply || "Done.", result },
        ]);
        if (shouldCloseChatForStoreNavigation(result)) {
          setOpen(false);
        }
      } catch (err) {
        const message =
          err instanceof ApiRateLimitError
            ? err.message
            : "Something went wrong. Please try again.";
        setTurns((prev) => [
          ...prev,
          { role: "assistant", content: message },
        ]);
      } finally {
        setLoading(false);
      }
    },
    [input, loading, turns, contextSku, contextOrderId, problems.active_scenario],
  );

  const openChat = useCallback((opts?: OpenChatOpts) => {
    if (opts?.seed) {
      // New conversation — avoids stacking a demo seed (e.g. Concierge band) onto the persisted history.
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
    // seed and context can arrive together (e.g. "Ask via chat" on a DELIVERED order).
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

/** Releases sku/orderId from the chat when the page component unmounts (e.g. leaving the PDP or the detail page). */
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
