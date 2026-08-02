import { Fragment, useEffect, useRef, useState } from "react";
import type { ChatMessage } from "../api";

// The tutor writes replies using WhatsApp's own *bold* convention (see
// backend app.agents.tutor_agent's system prompt) — WhatsApp's client
// renders that natively, but a plain <div> here would just show the raw
// asterisks. Split on *...* pairs and render matches as <strong> instead of
// pulling in a full markdown parser for what's really just one convention.
function formatMessage(text: string) {
  const parts = text.split(/(\*[^*\n]+\*)/g);
  return parts.map((part, i) => {
    if (part.length > 1 && part.startsWith("*") && part.endsWith("*")) {
      return <strong key={i}>{part.slice(1, -1)}</strong>;
    }
    return <Fragment key={i}>{part}</Fragment>;
  });
}

interface ChatWindowProps {
  fetchHistory: () => Promise<ChatMessage[]>;
  sendMessage: (message: string) => Promise<{ reply: string; credit_balance: number }>;
  onBalanceChange?: (balance: number) => void;
  // Optional — only the student chat app has these endpoints wired up
  // server-side today (see backend app.routers.student_app); when omitted
  // (e.g. the teacher's own "My AI Tutor" page), the attach/mic buttons
  // simply don't render rather than pointing at a route that doesn't exist.
  onSendImage?: (file: File) => Promise<{ reply: string; credit_balance: number }>;
  onSendVoice?: (blob: Blob, filename: string) => Promise<{ reply: string; credit_balance: number }>;
  onSendDocument?: (file: File) => Promise<{ reply: string; credit_balance: number }>;
}

const MAX_RECORDING_MS = 120_000;

export default function ChatWindow({
  fetchHistory, sendMessage, onBalanceChange, onSendImage, onSendVoice, onSendDocument,
}: ChatWindowProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [sendingLabel, setSendingLabel] = useState("Thinking...");
  const [error, setError] = useState<string | null>(null);
  const [recording, setRecording] = useState(false);
  const [recordSeconds, setRecordSeconds] = useState(0);
  const bottomRef = useRef<HTMLDivElement>(null);
  const imageInputRef = useRef<HTMLInputElement>(null);
  const documentInputRef = useRef<HTMLInputElement>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const recordedChunksRef = useRef<Blob[]>([]);
  const recordTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    fetchHistory().then(setMessages);
  }, [fetchHistory]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, sending]);

  useEffect(() => {
    return () => {
      if (recordTimerRef.current) clearInterval(recordTimerRef.current);
      mediaRecorderRef.current?.stream.getTracks().forEach((t) => t.stop());
    };
  }, []);

  function appendReply(userLabel: string, run: () => Promise<{ reply: string; credit_balance: number }>, label: string) {
    setError(null);
    setMessages((prev) => [...prev, { role: "user", message: userLabel, created_at: new Date().toISOString() }]);
    setSending(true);
    setSendingLabel(label);
    run()
      .then(({ reply, credit_balance }) => {
        setMessages((prev) => [...prev, { role: "assistant", message: reply, created_at: new Date().toISOString() }]);
        onBalanceChange?.(credit_balance);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Something went wrong"))
      .finally(() => setSending(false));
  }

  async function handleSend(e: React.FormEvent) {
    e.preventDefault();
    if (!input.trim() || sending) return;
    const text = input;
    setInput("");
    appendReply(text, () => sendMessage(text), "Thinking...");
  }

  function handleImagePicked(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file || !onSendImage) return;
    appendReply("📷 Photo", () => onSendImage(file), "Reading your photo...");
  }

  function handleDocumentPicked(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file || !onSendDocument) return;
    appendReply(`📄 ${file.name}`, () => onSendDocument(file), "Reading your file...");
  }

  async function startRecording() {
    if (!onSendVoice || recording) return;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      recordedChunksRef.current = [];
      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) recordedChunksRef.current.push(e.data);
      };
      recorder.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        if (recordTimerRef.current) clearInterval(recordTimerRef.current);
        const blob = new Blob(recordedChunksRef.current, { type: recorder.mimeType || "audio/webm" });
        if (blob.size > 0) {
          appendReply("🎤 Voice message", () => onSendVoice(blob, "voice_note.webm"), "Listening to your question...");
        }
      };
      mediaRecorderRef.current = recorder;
      recorder.start();
      setRecording(true);
      setRecordSeconds(0);
      recordTimerRef.current = setInterval(() => {
        setRecordSeconds((s) => {
          if ((s + 1) * 1000 >= MAX_RECORDING_MS) {
            stopRecording();
          }
          return s + 1;
        });
      }, 1000);
    } catch {
      setError("Couldn't access your microphone — check your browser's permission for this site.");
    }
  }

  function stopRecording() {
    mediaRecorderRef.current?.stop();
    setRecording(false);
  }

  const canAttach = !sending && !recording;

  return (
    <div className="chat-window">
      <div className="chat-messages">
        {messages.length === 0 && <p className="muted">Ask me anything — homework help, explanations, or a quick quiz!</p>}
        {messages.map((m, i) => (
          <div key={i} className={`chat-bubble chat-bubble-${m.role}`}>
            {formatMessage(m.message)}
          </div>
        ))}
        {sending && <div className="chat-bubble chat-bubble-assistant chat-bubble-typing">{sendingLabel}</div>}
        <div ref={bottomRef} />
      </div>
      {error && <p className="error">{error}</p>}
      {recording && (
        <p className="chat-recording-indicator">
          🔴 Recording… {String(Math.floor(recordSeconds / 60)).padStart(2, "0")}:{String(recordSeconds % 60).padStart(2, "0")}
          <button type="button" onClick={stopRecording} className="chat-recording-stop">
            Stop &amp; send
          </button>
        </p>
      )}
      <form onSubmit={handleSend} className="chat-input-row">
        {onSendImage && (
          <>
            <input
              ref={imageInputRef} type="file" accept="image/*" style={{ display: "none" }}
              onChange={handleImagePicked}
            />
            <button
              type="button" className="chat-icon-button" title="Attach a photo of your question"
              disabled={!canAttach} onClick={() => imageInputRef.current?.click()}
            >
              📷
            </button>
          </>
        )}
        {onSendDocument && (
          <>
            <input
              ref={documentInputRef} type="file" accept=".pdf,.docx" style={{ display: "none" }}
              onChange={handleDocumentPicked}
            />
            <button
              type="button" className="chat-icon-button" title="Attach a PDF or Word worksheet"
              disabled={!canAttach} onClick={() => documentInputRef.current?.click()}
            >
              📎
            </button>
          </>
        )}
        {onSendVoice && (
          <button
            type="button"
            className={`chat-icon-button${recording ? " chat-icon-button-active" : ""}`}
            title={recording ? "Stop recording" : "Record a voice question"}
            disabled={sending} onClick={recording ? stopRecording : startRecording}
          >
            {recording ? "⏹️" : "🎤"}
          </button>
        )}
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
