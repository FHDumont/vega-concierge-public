"use client";
// Widget flutuante WhatsApp-style — painel multi-turn global (F-051).
import { useShop } from "@/lib/store";
import { useChat } from "@/lib/chat-context";
import ChatThread from "@/components/ChatThread";

export default function ConciergeLauncher() {
  const shop = useShop();
  const chat = useChat();

  return (
    <>
      {chat.open && (
        <div className="ns-fab-panel" role="dialog" aria-label="AI chat">
          <header className="ns-fab-head">
            <div className="ttl">
              <span className="ns-spark sm" aria-hidden>✦</span>
              <div>
                <b>AI Chat</b>
                <span className="sub">Ask anything — recommendations, returns, search…</span>
              </div>
            </div>
            <div className="ns-fab-head-actions">
              <button
                type="button"
                className="ns-btn-ghost sm"
                onClick={chat.clearSession}
                disabled={chat.loading || chat.turns.length === 0}
                aria-label="Clear chat"
                title="Clear chat"
              >
                Clear
              </button>
              <button type="button" className="ns-btn-ghost sm" onClick={chat.closeChat} aria-label="Close chat">
                ✕
              </button>
            </div>
          </header>
          <div className="ns-fab-body ns-fab-chat">
            <ChatThread
              turns={chat.turns}
              loading={chat.loading}
              active={chat.open}
              onAdd={shop.addToCart}
            />
            <form
              className="ns-fab-composer"
              onSubmit={(e) => {
                e.preventDefault();
                chat.send();
              }}
            >
              <div className="ns-fab-composer-row">
                <input
                  className="ns-input"
                  value={chat.input}
                  onChange={(e) => chat.setInput(e.target.value)}
                  placeholder="Type your message…"
                  aria-label="Chat message"
                  disabled={chat.loading}
                />
                <button
                  type="submit"
                  className="ns-btn-primary sm"
                  disabled={chat.loading || !chat.input.trim()}
                >
                  Send
                </button>
              </div>
              {(chat.contextSku || chat.contextOrderId) && (
                <div className="ns-fab-composer-meta">
                  {chat.contextSku && (
                    <span className="ns-chip" title="Product context">
                      {chat.contextSku}
                    </span>
                  )}
                  {chat.contextOrderId && (
                    <span className="ns-chip" title="Order context">
                      {chat.contextOrderId}
                    </span>
                  )}
                </div>
              )}
            </form>
          </div>
        </div>
      )}
      <button
        type="button"
        className={`ns-fab ${chat.open ? "on" : ""}`}
        onClick={chat.toggleChat}
        aria-label={chat.open ? "Close AI chat" : "Open AI chat"}
        aria-expanded={chat.open}
      >
        <span aria-hidden>✦</span>
      </button>
    </>
  );
}
