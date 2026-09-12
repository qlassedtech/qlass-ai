import { useEffect, useRef, useState } from "react";
import { useOutletContext } from "react-router-dom";
import { WS_BASE, getStudentToken, type StudentProfile } from "../api";

type Context = { student: StudentProfile | null };

type LogEntry = { who: "you" | "tutor"; text: string; note?: string };

type CallStatus = "connecting" | "open" | "closed" | "error" | "mic_denied";

// Whatever the browser's MediaRecorder actually supports, preferring Opus
// inside WebM — backend app.services.sarvam_client._AUDIO_CONTENT_TYPES
// maps the ".webm" extension straight to "audio/webm", which Sarvam's STT
// accepts, so any browser default that produces a webm container works
// with no server-side changes.
function pickRecorderMimeType(): string {
  const candidates = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus"];
  for (const type of candidates) {
    if (typeof MediaRecorder !== "undefined" && MediaRecorder.isTypeSupported?.(type)) return type;
  }
  return "";
}

export default function Call() {
  const { student } = useOutletContext<Context>();

  const [status, setStatus] = useState<CallStatus>("connecting");
  const [recording, setRecording] = useState(false);
  const [busy, setBusy] = useState(false); // server is transcribing/thinking/synthesizing this turn
  const [log, setLog] = useState<LogEntry[]>([]);
  const [errorNote, setErrorNote] = useState<string | null>(null);

  const orbRef = useRef<HTMLDivElement | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const rafRef = useRef<number | null>(null);
  const playbackAudioRef = useRef<HTMLAudioElement | null>(null);

  // --- WebSocket lifecycle -------------------------------------------------
  useEffect(() => {
    const token = getStudentToken();
    if (!token) {
      setStatus("error");
      setErrorNote("You're not logged in — please log in again.");
      return;
    }

    const ws = new WebSocket(`${WS_BASE}/ws/voice-call?token=${encodeURIComponent(token)}`);
    ws.binaryType = "arraybuffer";
    wsRef.current = ws;

    ws.onopen = () => setStatus("open");
    ws.onerror = () => {
      setStatus("error");
      setErrorNote("Couldn't connect to the tutor right now — please check your connection and try again.");
    };
    ws.onclose = () => setStatus((prev) => (prev === "error" ? prev : "closed"));

    ws.onmessage = (event) => {
      if (typeof event.data === "string") {
        let frame: Record<string, unknown>;
        try {
          frame = JSON.parse(event.data);
        } catch {
          return;
        }
        if (frame.type === "transcript") {
          setLog((prev) => [...prev, { who: "you", text: String(frame.text ?? "") }]);
        } else if (frame.type === "reply_text") {
          setLog((prev) => [...prev, { who: "tutor", text: String(frame.text ?? "") }]);
        } else if (frame.type === "tts_failed") {
          setLog((prev) => {
            const copy = [...prev];
            const last = copy[copy.length - 1];
            if (last && last.who === "tutor") last.note = "voice reply unavailable, here's the text";
            return copy;
          });
          setBusy(false);
        } else if (frame.type === "error") {
          setErrorNote(String(frame.message ?? "Something went wrong."));
          setBusy(false);
        }
      } else {
        // Binary frame: the synthesized reply audio.
        const blob = new Blob([event.data], { type: "audio/opus" });
        playAssistantAudio(blob);
        setErrorNote(null);
        setBusy(false);
      }
    };

    return () => {
      ws.close();
      stopMicVisualizer();
      recorderRef.current?.stream.getTracks().forEach((t) => t.stop());
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // --- Mic amplitude visualization (while recording) ---------------------
  function startMicVisualizer(stream: MediaStream) {
    const AudioCtx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    const ctx = new AudioCtx();
    const source = ctx.createMediaStreamSource(stream);
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 256;
    source.connect(analyser);
    audioCtxRef.current = ctx;
    analyserRef.current = analyser;
    runVisualizerLoop();
  }

  // --- Assistant playback amplitude visualization -------------------------
  function playAssistantAudio(blob: Blob) {
    stopMicVisualizer(); // in case the analyser context is still the mic's
    const url = URL.createObjectURL(blob);
    const audioEl = new Audio(url);
    playbackAudioRef.current = audioEl;

    const AudioCtx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    const ctx = new AudioCtx();
    const source = ctx.createMediaElementSource(audioEl);
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 256;
    source.connect(analyser);
    analyser.connect(ctx.destination);
    audioCtxRef.current = ctx;
    analyserRef.current = analyser;
    runVisualizerLoop();

    audioEl.onended = () => {
      stopMicVisualizer();
      URL.revokeObjectURL(url);
    };
    audioEl.play().catch(() => {
      // Autoplay can be blocked before any user gesture on first load —
      // by the time this fires the student has already tapped the talk
      // button once, so this is rare, but fail quietly rather than crash.
    });
  }

  function runVisualizerLoop() {
    const analyser = analyserRef.current;
    const orb = orbRef.current;
    if (!analyser || !orb) return;
    const data = new Uint8Array(analyser.frequencyBinCount);

    function tick() {
      if (!analyserRef.current || !orbRef.current) return;
      analyserRef.current.getByteFrequencyData(data);
      const avg = data.reduce((sum, v) => sum + v, 0) / data.length; // 0-255
      const scale = 1 + Math.min(avg / 255, 1) * 0.45;
      const glow = 20 + Math.min(avg / 255, 1) * 60;
      orbRef.current.style.transform = `scale(${scale})`;
      orbRef.current.style.boxShadow = `0 0 ${glow}px ${glow / 2}px var(--call-orb-glow)`;
      rafRef.current = requestAnimationFrame(tick);
    }
    rafRef.current = requestAnimationFrame(tick);
  }

  function stopMicVisualizer() {
    if (rafRef.current) cancelAnimationFrame(rafRef.current);
    rafRef.current = null;
    analyserRef.current = null;
    if (audioCtxRef.current) {
      audioCtxRef.current.close().catch(() => {});
      audioCtxRef.current = null;
    }
    if (orbRef.current) {
      orbRef.current.style.transform = "scale(1)";
      orbRef.current.style.boxShadow = "";
    }
  }

  // --- Push-to-talk (tap-to-start / tap-to-stop toggle) -------------------
  async function startRecording() {
    setErrorNote(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      mediaStreamRef.current = stream;
      const mimeType = pickRecorderMimeType();
      const recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
      const chunks: BlobPart[] = [];
      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunks.push(e.data);
      };
      recorder.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        stopMicVisualizer();
        const blob = new Blob(chunks, { type: mimeType || "audio/webm" });
        sendTurn(blob);
      };
      recorderRef.current = recorder;
      recorder.start();
      startMicVisualizer(stream);
      setRecording(true);
    } catch {
      setStatus("mic_denied");
      setErrorNote("Couldn't access your microphone — please allow microphone access and try again.");
    }
  }

  function stopRecording() {
    recorderRef.current?.stop();
    setRecording(false);
  }

  async function sendTurn(blob: Blob) {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      setErrorNote("Not connected to the tutor right now — please wait a moment and try again.");
      return;
    }
    setBusy(true);
    const buffer = await blob.arrayBuffer();
    ws.send(buffer);
  }

  function handleTalkButtonClick() {
    if (busy || status !== "open") return;
    if (recording) stopRecording();
    else startRecording();
  }

  const talkLabel = recording ? "Tap to stop" : busy ? "Thinking…" : "Tap to talk";

  return (
    <div>
      <style>{`
        @keyframes call-orb-idle-pulse {
          0%, 100% { transform: scale(1); }
          50% { transform: scale(1.06); }
        }
        .call-orb {
          width: 200px; height: 200px; border-radius: 50%;
          background: radial-gradient(circle at 35% 30%, var(--call-orb-light), var(--call-orb-dark));
          margin: 24px auto; transition: box-shadow 0.1s ease-out;
          animation: call-orb-idle-pulse 3.2s ease-in-out infinite;
        }
        .call-orb.recording, .call-orb.speaking { animation: none; }
        .call-talk-btn {
          display: block; margin: 0 auto; padding: 12px 28px; border-radius: 999px;
          border: none; font-size: 15px; font-weight: 600; cursor: pointer;
          background: var(--call-btn-bg); color: var(--call-btn-fg);
        }
        .call-talk-btn:disabled { opacity: 0.5; cursor: not-allowed; }
        .call-log { max-width: 480px; margin: 24px auto 0; display: flex; flex-direction: column; gap: 10px; }
        .call-log-entry { padding: 10px 14px; border-radius: 10px; font-size: 14px; line-height: 1.4; }
        .call-log-entry.you { background: var(--call-log-you-bg); align-self: flex-end; }
        .call-log-entry.tutor { background: var(--call-log-tutor-bg); align-self: flex-start; }
        .call-log-note { font-size: 12px; opacity: 0.7; margin-top: 4px; }
      `}</style>

      <div className="page-header">
        <div>
          <h1>Talk to your AI tutor</h1>
          <p>Tap the button, ask your question out loud, then tap again — your tutor will answer back with voice.</p>
        </div>
      </div>

      <div
        className="card"
        style={{
          maxWidth: 560, margin: "0 auto 16px", padding: "10px 16px", fontSize: 13,
        }}
      >
        ⚠️ This call uses a lot more data than a WhatsApp voice note. If your connection is slow, texting or
        sending a voice note to your tutor on WhatsApp will work better.
      </div>

      {status === "connecting" && <p className="muted" style={{ textAlign: "center" }}>Connecting…</p>}
      {status === "closed" && (
        <p className="error" style={{ textAlign: "center" }}>
          The call ended. Refresh the page to start a new one.
        </p>
      )}
      {status === "mic_denied" && (
        <p className="error" style={{ textAlign: "center" }}>
          {errorNote ?? "Microphone access is blocked — allow it in your browser settings and refresh."}
        </p>
      )}
      {status === "error" && !errorNote && (
        <p className="error" style={{ textAlign: "center" }}>Couldn't connect — please refresh and try again.</p>
      )}
      {errorNote && status !== "mic_denied" && (
        <p className="error" style={{ textAlign: "center" }}>{errorNote}</p>
      )}

      <div ref={orbRef} className={`call-orb${recording ? " recording" : ""}`} />

      <button
        className="call-talk-btn"
        onClick={handleTalkButtonClick}
        disabled={status !== "open" || busy}
      >
        {talkLabel}
      </button>

      <div className="call-log">
        {log.length === 0 && (
          <p className="muted" style={{ textAlign: "center" }}>
            {student ? `Hi ${student.name.split(" ")[0]}, tap the button above to start talking.` : ""}
          </p>
        )}
        {log.map((entry, i) => (
          <div key={i} className={`call-log-entry ${entry.who}`}>
            <strong>{entry.who === "you" ? "You" : "Tutor"}:</strong> {entry.text}
            {entry.note && <div className="call-log-note">{entry.note}</div>}
          </div>
        ))}
      </div>

      <style>{`
        :root { --call-orb-light: #93c5fd; --call-orb-dark: #2563eb; --call-orb-glow: rgba(37,99,235,0.55); --call-btn-bg: #2563eb; --call-btn-fg: #fff; --call-log-you-bg: #eef2ff; --call-log-tutor-bg: #f0fdf4; }
        @media (prefers-color-scheme: dark) {
          :root:not([data-theme="light"]) { --call-orb-light: #60a5fa; --call-orb-dark: #1d4ed8; --call-orb-glow: rgba(96,165,250,0.6); --call-log-you-bg: #1e293b; --call-log-tutor-bg: #14291f; }
        }
        :root[data-theme="dark"] { --call-orb-light: #60a5fa; --call-orb-dark: #1d4ed8; --call-orb-glow: rgba(96,165,250,0.6); --call-log-you-bg: #1e293b; --call-log-tutor-bg: #14291f; }
      `}</style>
    </div>
  );
}
