import { useEffect, useRef, useState } from "react";
import { useOutletContext } from "react-router-dom";
import { WS_BASE, getStudentToken, type StudentProfile } from "../api";

type Context = { student: StudentProfile | null };

type VideoRef = { title: string; url: string };
type LogEntry = { who: "you" | "tutor"; text: string; note?: string; video?: VideoRef };

type CallStatus = "connecting" | "open" | "closed" | "error" | "mic_denied";

// Mirrors the drawing-primitive schema backend/app/services/sketch_client.py
// asks the model for — see its module docstring for the authoritative
// shape. Deliberately loose here (unknown fields just get ignored below)
// since this is untrusted-ish LLM-generated data passed through a network
// hop, not a contract either side can fully guarantee at the type level.
type DiagramElement =
  | { type: "rect"; x: number; y: number; w: number; h: number }
  | { type: "ellipse"; x: number; y: number; rx: number; ry: number }
  | { type: "line"; points: [number, number][] }
  | { type: "arrow"; x1: number; y1: number; x2: number; y2: number }
  | { type: "text"; x: number; y: number; text: string };

// Fixed 400x300 coordinate space, matching the backend's canvas assumption
// exactly so no client-side scaling/translation is needed.
const DIAGRAM_W = 400;
const DIAGRAM_H = 300;

// rough.js is loaded lazily, only on this page (see loadRoughJs below),
// rather than an npm dependency/import or a global <script> tag in
// index.html — it's used nowhere else in this app, and every other page
// (Join, Login, the teacher portal) would otherwise pay for ~36KB of JS on
// every load for a feature only this one page uses, which cuts against
// this app's own low-bandwidth design for its actual audience.
declare global {
  interface Window {
    rough?: {
      canvas: (canvas: HTMLCanvasElement) => {
        rectangle: (x: number, y: number, w: number, h: number, opts?: Record<string, unknown>) => void;
        ellipse: (x: number, y: number, w: number, h: number, opts?: Record<string, unknown>) => void;
        line: (x1: number, y1: number, x2: number, y2: number, opts?: Record<string, unknown>) => void;
        linearPath: (points: [number, number][], opts?: Record<string, unknown>) => void;
      };
    };
  }
}

const ROUGH_JS_SRC = "https://cdnjs.cloudflare.com/ajax/libs/rough.js/3.1.0/rough.umd.js";

// Loads rough.js once and caches the in-flight/completed promise on
// `window` itself (not a module-level variable) so remounting this page
// (e.g. navigating away and back) never injects a second <script> tag or
// re-fetches it — a plain HTML script tag has no built-in de-dup the way
// an ES module import would. Resolves to true/false rather than
// rejecting, since a failed load must never throw into a caller — the
// diagram feature degrades to "no diagram" silently either way.
function loadRoughJs(): Promise<boolean> {
  const w = window as unknown as { __roughJsLoad?: Promise<boolean> };
  if (w.__roughJsLoad) return w.__roughJsLoad;
  if (window.rough) return (w.__roughJsLoad = Promise.resolve(true));

  w.__roughJsLoad = new Promise((resolve) => {
    const script = document.createElement("script");
    script.src = ROUGH_JS_SRC;
    script.onload = () => resolve(true);
    script.onerror = () => resolve(false);
    document.head.appendChild(script);
  });
  return w.__roughJsLoad;
}

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
  const diagramCanvasRef = useRef<HTMLCanvasElement | null>(null);
  // Bumped every time a new diagram scene starts animating, and checked by
  // every pending setTimeout callback before it draws — the same
  // defensive "did something newer supersede me" pattern this file's own
  // WS message handling already leans on elsewhere (e.g. process_message's
  // own "a newer message already superseded this one" case), so a second
  // diagram arriving mid-animation cleanly abandons the first rather than
  // the two animations interleaving their draws on the same canvas.
  const diagramGenerationRef = useRef(0);
  const diagramTimeoutsRef = useRef<number[]>([]);
  // "video" and "reply_text" frames can arrive in either order (see
  // voice_call.py's module docstring) — this holds a video that arrived
  // BEFORE the tutor log entry it belongs to exists yet, so it can be
  // attached the moment that entry is created instead of being dropped.
  const pendingVideoRef = useRef<VideoRef | null>(null);

  // Kicked off as soon as this page mounts, in parallel with the WebSocket
  // connecting below — by the time a "diagram" frame could plausibly
  // arrive (after mic permission, a full record-transcribe-tutor round
  // trip), rough.js has almost always already finished loading. If it
  // hasn't, renderDiagram just silently does nothing for that one frame.
  useEffect(() => {
    loadRoughJs();
  }, []);

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
          const video = pendingVideoRef.current ?? undefined;
          pendingVideoRef.current = null;
          setLog((prev) => [...prev, { who: "tutor", text: String(frame.text ?? ""), video }]);
        } else if (frame.type === "video") {
          const video: VideoRef = { title: String(frame.title ?? "Video"), url: String(frame.url ?? "") };
          setLog((prev) => {
            const copy = [...prev];
            const last = copy[copy.length - 1];
            if (last && last.who === "tutor") {
              // reply_text already arrived — attach directly.
              copy[copy.length - 1] = { ...last, video };
              return copy;
            }
            // reply_text hasn't arrived yet — stash it for the handler above.
            pendingVideoRef.current = video;
            return prev;
          });
        } else if (frame.type === "diagram") {
          renderDiagram(Array.isArray(frame.scene) ? (frame.scene as DiagramElement[]) : []);
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
      diagramGenerationRef.current += 1; // abandon any in-flight diagram animation
      diagramTimeoutsRef.current.forEach((id) => window.clearTimeout(id));
      diagramTimeoutsRef.current = [];
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // --- Diagram sketch rendering (rough.js) --------------------------------
  // Reveals `scene`'s elements one at a time, in array order (order is
  // meaningful — see sketch_client's system prompt: outline shapes are
  // generated before their labels), paced evenly over roughly 3-6 seconds
  // total so it reads as a tutor "drawing while explaining" rather than a
  // diagram just popping in. Fails completely silently (no error shown to
  // the student) if rough.js didn't load or the canvas isn't mounted —
  // this is a nice-to-have on top of the core voice flow, never allowed to
  // block or visibly break it.
  function renderDiagram(scene: DiagramElement[]) {
    const canvas = diagramCanvasRef.current;
    const rough = window.rough;
    if (!canvas || !rough || scene.length === 0) return;

    let rc: ReturnType<NonNullable<Window["rough"]>["canvas"]>;
    let ctx: CanvasRenderingContext2D | null;
    try {
      rc = rough.canvas(canvas);
      ctx = canvas.getContext("2d");
    } catch {
      return;
    }
    if (!ctx) return;

    // Supersede any previous animation still in flight.
    diagramGenerationRef.current += 1;
    const generation = diagramGenerationRef.current;
    diagramTimeoutsRef.current.forEach((id) => window.clearTimeout(id));
    diagramTimeoutsRef.current = [];

    ctx.clearRect(0, 0, canvas.width, canvas.height);

    const totalDurationMs = 4500; // within the ~3-6s target
    const stepMs = Math.max(150, totalDurationMs / scene.length);

    scene.forEach((element, index) => {
      const timeoutId = window.setTimeout(() => {
        // A newer diagram (or an unmounted page) already took over — don't
        // draw a stale element on top of whatever's there now.
        if (diagramGenerationRef.current !== generation) return;
        drawDiagramElement(rc, ctx!, element);
      }, index * stepMs);
      diagramTimeoutsRef.current.push(timeoutId);
    });
  }

  function drawDiagramElement(
    rc: ReturnType<NonNullable<Window["rough"]>["canvas"]>,
    ctx: CanvasRenderingContext2D,
    element: DiagramElement,
  ) {
    try {
      switch (element.type) {
        case "rect":
          rc.rectangle(element.x, element.y, element.w, element.h);
          break;
        case "ellipse":
          // rough.js takes a center point plus full width/height, not a
          // radius — the backend schema gives radii, so double them here.
          rc.ellipse(element.x, element.y, element.rx * 2, element.ry * 2);
          break;
        case "line":
          if (element.points.length >= 2) rc.linearPath(element.points);
          break;
        case "arrow": {
          rc.line(element.x1, element.y1, element.x2, element.y2);
          // rough.js has no arrowhead primitive — draw one manually as two
          // short lines angled back from the endpoint.
          const angle = Math.atan2(element.y2 - element.y1, element.x2 - element.x1);
          const headLen = 10;
          const spread = Math.PI / 7;
          rc.line(
            element.x2,
            element.y2,
            element.x2 - headLen * Math.cos(angle - spread),
            element.y2 - headLen * Math.sin(angle - spread),
          );
          rc.line(
            element.x2,
            element.y2,
            element.x2 - headLen * Math.cos(angle + spread),
            element.y2 - headLen * Math.sin(angle + spread),
          );
          break;
        }
        case "text": {
          ctx.font = "14px sans-serif";
          // The model is told to space labels apart, but it occasionally
          // misjudges text width and places two labels close enough to
          // overlap into an unreadable smear. A translucent white backing
          // box drawn UNDER each label (each one drawn in array order, so
          // a later label's halo sits on top of an earlier label's text)
          // keeps whichever label was placed last fully legible even when
          // they collide — imperfect, but strictly better than two
          // half-obscured labels blending together.
          const metrics = ctx.measureText(element.text);
          const padX = 3, padY = 2;
          ctx.fillStyle = "rgba(253, 253, 251, 0.88)";
          ctx.fillRect(
            element.x - padX, element.y - 11 - padY, metrics.width + padX * 2, 14 + padY * 2,
          );
          // canvas fillStyle can't resolve a CSS custom property, and the
          // diagram is drawn on a fixed light background (see .call-
          // diagram-canvas below) regardless of page theme, so a plain
          // fixed dark color is used rather than trying to theme it.
          ctx.fillStyle = "#333333";
          ctx.fillText(element.text, element.x, element.y);
          break;
        }
      }
    } catch {
      // One malformed element (out-of-range values rough.js chokes on,
      // etc.) should not stop the rest of the scene from drawing.
    }
  }

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
        .call-log-video { font-size: 13px; margin-top: 6px; }
        .call-log-video a { color: inherit; text-decoration: underline; }
        .call-diagram-canvas {
          display: block; max-width: 100%; height: auto; width: 400px;
          margin: 8px auto 20px; background: #fdfdfb; border-radius: 10px;
          border: 1px solid var(--call-diagram-border);
        }
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

      <canvas
        ref={diagramCanvasRef}
        className="call-diagram-canvas"
        width={DIAGRAM_W}
        height={DIAGRAM_H}
      />

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
            {entry.video && (
              <div className="call-log-video">
                📺{" "}
                <a href={entry.video.url} target="_blank" rel="noopener noreferrer">
                  {entry.video.title}
                </a>
              </div>
            )}
          </div>
        ))}
      </div>

      <style>{`
        :root { --call-orb-light: #93c5fd; --call-orb-dark: #2563eb; --call-orb-glow: rgba(37,99,235,0.55); --call-btn-bg: #2563eb; --call-btn-fg: #fff; --call-log-you-bg: #eef2ff; --call-log-tutor-bg: #f0fdf4; --call-diagram-border: #e2e2df; }
        @media (prefers-color-scheme: dark) {
          :root:not([data-theme="light"]) { --call-orb-light: #60a5fa; --call-orb-dark: #1d4ed8; --call-orb-glow: rgba(96,165,250,0.6); --call-log-you-bg: #1e293b; --call-log-tutor-bg: #14291f; --call-diagram-border: #3f3f3f; }
        }
        :root[data-theme="dark"] { --call-orb-light: #60a5fa; --call-orb-dark: #1d4ed8; --call-orb-glow: rgba(96,165,250,0.6); --call-log-you-bg: #1e293b; --call-log-tutor-bg: #14291f; --call-diagram-border: #3f3f3f; }
      `}</style>
    </div>
  );
}
