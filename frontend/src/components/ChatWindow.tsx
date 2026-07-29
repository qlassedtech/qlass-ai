import { useEffect, useRef, useState } from "react";
import type { ChatMessage } from "../api";

interface ChatWindowProps {
  fetchHistory: () => Promise<ChatMessage[]>;
  sendMessage: (message: string) => Promise<{ reply: string; credit_balance: number }>;
  onBalanceChange?: (balance: number) => void;
}

export default function ChatWindow({ fetchHistory, sendMessage, onBalanceChange }: ChatWindowProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetchHistory().then(setMessages);
  }, [fetchHistory]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function handleSend(e: React.FormEvent) {
    e.preventDefault();
    if (!input.trim() || sending) return;
    setError(null);
    const text = input;
    setInput("");
    setMessages((prev) => [...prev, { role: "user", message: text, created_at: new Date().toISOString() }]);
    setSending(true);
    try {
      const { reply, credit_balance } = await sendMessage(text);
      setMessages((prev) => [...prev, { role: "assistant", message: reply, created_at: new Date().toISOString() }]);
      onBalanceChange?.(credit_balance);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to send message");
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="chat-window">
      <div className="chat-messages">
        {messages.length === 0 && <p className="muted">Ask me anything — homework help, explanations, or a quick quiz!</p>}
        {messages.map((m, i) => (
          <div key={i} className={`chat-bubble chat-bubble-${m.role}`}>
            {m.message}
          </div>
        ))}
        {sending && <div className="chat-bubble chat-bubble-assistant chat-bubble-typing">Thinking...</div>}
        <div ref={bottomRef} />
      </div>
      {error && <p className="error">{error}</p>}
      <form onSubmit={handleSend} className="chat-input-row">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Type your question..."
          disabled={sending}
        />
        <button type="submit" disabled={sending || !input.trim()}>
          Send
        </button>
      </form>
    </div>
  );
}
