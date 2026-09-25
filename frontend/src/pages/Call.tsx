import { useEffect, useRef, useState } from "react";
import { useOutletContext } from "react-router-dom";
import { WS_BASE, errorMessage, isStudentAuthenticated, studentApi, type StudentProfile } from "../api";

type Context = { student: StudentProfile | null };

type VideoRef = { title: string; url: string };
type LogEntry = { who: "you" | "tutor"; text: string; note?: string; video?: VideoRef };

type CallStatus = "connecting" | "open" | "closed" | "error" | "mic_denied";

// Mirrors the drawing-primitive schema backend/app/services/sketch_client.py
// asks the model for — see its module docstring for the authoritative
// shape. Deliberately loose here (unknown fields just get ignored below)
// since this is untrusted-ish LLM-generated data passed through a network
// hop, not a contract either side can fully guarantee at the type level.
//
// Every optional field below is additive and backward compatible: an
// element without them renders exactly as it did before they existed.
// `color`/`width` are stroke color (hex) and stroke width; `fill` on
// rect/ellipse is a solid fill color. `branch` is the Buzan-style
// mind-map curve emitted by the backend's mind-map generator.
type DiagramElement =
  | { type: "rect"; x: number; y: number; w: number; h: number; color?: string; width?: number; fill?: string }
  | { type: "ellipse"; x: number; y: number; rx: number; ry: number; color?: string; width?: number; fill?: string }
  | { type: "line"; points: [number, number][]; color?: string; width?: number }
  | { type: "arrow"; x1: number; y1: number; x2: number; y2: number; color?: string; width?: number }
  | {
      type: "text";
      x: number;
      y: number;
      text: string;
      color?: string;
      size?: number;
      weight?: "normal" | "bold";
      align?: "left" | "center" | "right";
    }
  | {
      type: "branch";
      // [start, control, end] of a quadratic curve.
      points: [[number, number], [number, number], [number, number]];
      color: string;
      width: number;
      label?: string;
      level?: 1 | 2;
      label_at?: "mid" | "end";
    };

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
        path: (d: string, opts?: Record<string, unknown>) => void;
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
  const mouthRef = useRef<SVGEllipseElement | null>(null);
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
  // Skoolgpt logo stamped on every diagram; created lazily on first render
  // and cached so repeated diagrams never re-fetch it.
  const diagramLogoRef = useRef<HTMLImageElement | null>(null);
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
    if (!isStudentAuthenticated()) {
      setStatus("error");
      setErrorNote("You're not logged in — please log in again.");
      return;
    }

    // The student token itself never goes in the URL (URLs get logged) —
    // a 60-second single-use ticket is fetched over the normal
    // header-authenticated API right before connecting, and that's what
    // the backend's `?ticket=` handshake accepts. `cancelled` covers the
    // page unmounting while the ticket request is still in flight, so a
    // socket is never opened for a page that's already gone.
    let cancelled = false;
    let ws: WebSocket | null = null;

    (async () => {
      let ticket: string;
      try {
        ({ ticket } = await studentApi.voiceCallTicket());
      } catch (err) {
        if (cancelled) return;
        setStatus("error");
        setErrorNote(errorMessage(err, "Couldn't start the call right now — please refresh and try again."));
        return;
      }
      if (cancelled) return;
      ws = openSocket(ticket);
    })();

    function openSocket(ticket: string): WebSocket {
      const ws = new WebSocket(`${WS_BASE}/ws/voice-call?ticket=${encodeURIComponent(ticket)}`);
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
      return ws;
    }

    return () => {
      cancelled = true;
      ws?.close();
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
    drawDiagramLogo(ctx, canvas, generation);

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

  // Stamps the logo at the canvas's bottom-right corner. If the image is
  // still loading, it's painted from the onload handler instead — but only
  // if `generation` is still the current diagram, so a slow first load
  // never paints over a newer scene that has since cleared the canvas.
  function drawDiagramLogo(ctx: CanvasRenderingContext2D, canvas: HTMLCanvasElement, generation: number) {
    const paint = (img: HTMLImageElement) => {
      if (!img.naturalWidth || !img.naturalHeight) return;
      const w = 56;
      const h = (w * img.naturalHeight) / img.naturalWidth;
      const inset = 6;
      try {
        ctx.globalAlpha = 0.9;
        ctx.drawImage(img, canvas.width - w - inset, canvas.height - h - inset, w, h);
      } catch {
        // A broken/undecodable image must never break the diagram.
      } finally {
        ctx.globalAlpha = 1;
      }
    };

    let img = diagramLogoRef.current;
    if (!img) {
      img = new Image();
      img.src = "/logo-tight.png";
      diagramLogoRef.current = img;
    }
    if (img.complete) {
      paint(img);
      return;
    }
    img.addEventListener(
      "load",
      () => {
        if (diagramGenerationRef.current === generation) paint(img!);
      },
      { once: true },
    );
  }

  // Draws a text label with a translucent backing halo under it, sized and
  // positioned to follow the alignment/font — see the "text" case below
  // for why the halo exists. `y` is the text baseline.
  function drawHaloedText(
    ctx: CanvasRenderingContext2D,
    text: string,
    x: number,
    y: number,
    opts: { font: string; size: number; color: string; align: "left" | "center" | "right" },
  ) {
    ctx.font = opts.font;
    ctx.textAlign = opts.align;
    ctx.textBaseline = "alphabetic";
    const width = ctx.measureText(text).width;
    const left = opts.align === "center" ? x - width / 2 : opts.align === "right" ? x - width : x;
    const padX = 3, padY = 2;
    // Ascent ≈ 0.8em for typical sans-serif faces (14px → 11px, matching
    // the halo geometry this renderer has always used for 14px labels).
    const ascent = Math.round(opts.size * 0.8);
    ctx.fillStyle = "rgba(253, 253, 251, 0.88)";
    ctx.fillRect(left - padX, y - ascent - padY, width + padX * 2, opts.size + padY * 2);
    ctx.fillStyle = opts.color;
    ctx.fillText(text, x, y);
    ctx.textAlign = "left";
  }

  function drawDiagramElement(
    rc: ReturnType<NonNullable<Window["rough"]>["canvas"]>,
    ctx: CanvasRenderingContext2D,
    element: DiagramElement,
  ) {
    // Only pass stroke/fill options that were actually given, so elements
    // without them keep rough.js's defaults exactly as before.
    const strokeOpts = (el: { color?: string; width?: number; fill?: string }) => {
      const o: Record<string, unknown> = {};
      if (el.color) o.stroke = el.color;
      if (typeof el.width === "number") o.strokeWidth = el.width;
      if (el.fill) {
        o.fill = el.fill;
        o.fillStyle = "solid";
      }
      return o;
    };

    try {
      switch (element.type) {
        case "rect":
          rc.rectangle(element.x, element.y, element.w, element.h, strokeOpts(element));
          break;
        case "ellipse":
          // rough.js takes a center point plus full width/height, not a
          // radius — the backend schema gives radii, so double them here.
          rc.ellipse(element.x, element.y, element.rx * 2, element.ry * 2, strokeOpts(element));
          break;
        case "line":
          if (element.points.length >= 2) rc.linearPath(element.points, strokeOpts(element));
          break;
        case "arrow": {
          const opts = strokeOpts(element);
          rc.line(element.x1, element.y1, element.x2, element.y2, opts);
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
            opts,
          );
          rc.line(
            element.x2,
            element.y2,
            element.x2 - headLen * Math.cos(angle + spread),
            element.y2 - headLen * Math.sin(angle + spread),
            opts,
          );
          break;
        }
        case "text": {
          // The model is told to space labels apart, but it occasionally
          // misjudges text width and places two labels close enough to
          // overlap into an unreadable smear. A translucent white backing
          // box drawn UNDER each label (each one drawn in array order, so
          // a later label's halo sits on top of an earlier label's text)
          // keeps whichever label was placed last fully legible even when
          // they collide — imperfect, but strictly better than two
          // half-obscured labels blending together.
          //
          // `align` defaults to "left" deliberately: the backend's label
          // collision math assumes left-aligned text, so existing scenes
          // must keep rendering exactly as before. New (mind-map) scenes
          // opt into "center"/"right" explicitly.
          //
          // canvas fillStyle can't resolve a CSS custom property, and the
          // diagram is drawn on a fixed light background (see .call-
          // diagram-canvas below) regardless of page theme, so a plain
          // fixed dark color is the default rather than trying to theme it.
          const size = typeof element.size === "number" && element.size > 0 ? element.size : 14;
          const weight = element.weight === "bold" ? "bold " : "";
          drawHaloedText(ctx, element.text, element.x, element.y, {
            font: `${weight}${size}px sans-serif`,
            size,
            color: element.color || "#333333",
            align: element.align || "left",
          });
          break;
        }
        case "branch": {
          // Buzan-style mind-map branch: a quadratic curve from start via a
          // control point to end, drawn as one animation step together with
          // its label (a branch without its label reads as an unfinished
          // squiggle).
          if (!Array.isArray(element.points) || element.points.length < 3) break;
          const [[sx, sy], [cx, cy], [ex, ey]] = element.points;
          rc.path(`M ${sx} ${sy} Q ${cx} ${cy} ${ex} ${ey}`, {
            stroke: element.color,
            strokeWidth: element.width,
            roughness: 0.9,
            bowing: 0.6,
          });
          if (!element.label) break;
          const atEnd = element.label_at === "end" || (element.label_at !== "mid" && element.level === 2);
          if (atEnd) {
            // Level-2 (leaf) branches: small plain label at the branch tip.
            drawHaloedText(ctx, element.label, ex, ey - 6, {
              font: "11px sans-serif",
              size: 11,
              color: "#333333",
              align: "center",
            });
          } else {
            // Level-1 branches: bold label in the branch's own color,
            // riding along the curve at its midpoint (t = 0.5).
            const mx = 0.25 * sx + 0.5 * cx + 0.25 * ex;
            const my = 0.25 * sy + 0.5 * cy + 0.25 * ey;
            drawHaloedText(ctx, element.label, mx, my - 8, {
              font: "bold 13px sans-serif",
              size: 13,
              color: element.color,
              align: "center",
            });
          }
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

  // Resting (mouth-closed) and fully-open ellipse heights, in the face
  // SVG's own 0-200 coordinate space (see the <svg viewBox> below) — a
  // zero-cost stand-in for lip-sync: no per-frame image/video generation,
  // just mapping the same live amplitude value that used to only drive
  // the plain orb's scale/glow onto how open the mouth looks.
  const MOUTH_CLOSED_RY = 4;
  const MOUTH_OPEN_RY = 26;

  function runVisualizerLoop() {
    const analyser = analyserRef.current;
    const orb = orbRef.current;
    if (!analyser || !orb) return;
    const data = new Uint8Array(analyser.frequencyBinCount);

    function tick() {
      if (!analyserRef.current || !orbRef.current) return;
      analyserRef.current.getByteFrequencyData(data);
      const avg = data.reduce((sum, v) => sum + v, 0) / data.length; // 0-255
      const level = Math.min(avg / 255, 1);
      const scale = 1 + level * 0.08; // subtle now — the mouth carries most of the reaction
      const glow = 16 + level * 40;
      orbRef.current.style.transform = `scale(${scale})`;
      orbRef.current.style.boxShadow = `0 0 ${glow}px ${glow / 2}px var(--call-orb-glow)`;
      if (mouthRef.current) {
        mouthRef.current.setAttribute("ry", String(MOUTH_CLOSED_RY + level * (MOUTH_OPEN_RY - MOUTH_CLOSED_RY)));
      }
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
    if (mouthRef.current) {
      mouthRef.current.setAttribute("ry", String(MOUTH_CLOSED_RY));
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
          position: relative;
        }
        .call-orb.recording, .call-orb.speaking { animation: none; }
        /* The "avatar" itself — a zero-cost stand-in for a video avatar.
           Eyes blink on a fixed CSS timer (no JS needed); the mouth's ry
           is driven imperatively in runVisualizerLoop's tick() from the
           same live mic/playback amplitude that used to only scale/glow
           the plain orb — see MOUTH_CLOSED_RY/MOUTH_OPEN_RY above. */
        .call-face { position: absolute; inset: 0; width: 100%; height: 100%; }
        .call-face-eyes ellipse { fill: var(--call-face-feature); animation: call-face-blink 4.4s ease-in-out infinite; transform-origin: center; transform-box: fill-box; }
        .call-face-eyes ellipse:nth-child(2) { animation-delay: 0.08s; }
        .call-face-mouth { fill: var(--call-face-feature); transition: ry 0.05s linear; }
        @keyframes call-face-blink {
          0%, 92%, 100% { transform: scaleY(1); }
          96% { transform: scaleY(0.12); }
        }
        @media (prefers-reduced-motion: reduce) {
          .call-face-eyes ellipse { animation: none; }
        }
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

      <div ref={orbRef} className={`call-orb${recording ? " recording" : ""}`}>
        <svg className="call-face" viewBox="0 0 200 200" aria-hidden="true">
          <g className="call-face-eyes">
            <ellipse cx="70" cy="80" rx="11" ry="14" />
            <ellipse cx="130" cy="80" rx="11" ry="14" />
          </g>
          <ellipse ref={mouthRef} className="call-face-mouth" cx="100" cy="132" rx="26" ry="4" />
        </svg>
      </div>

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
        :root { --call-orb-light: #93c5fd; --call-orb-dark: #2563eb; --call-orb-glow: rgba(37,99,235,0.55); --call-btn-bg: #2563eb; --call-btn-fg: #fff; --call-log-you-bg: #eef2ff; --call-log-tutor-bg: #f0fdf4; --call-diagram-border: #e2e2df; --call-face-feature: #1c2a5e; }
        @media (prefers-color-scheme: dark) {
          :root:not([data-theme="light"]) { --call-orb-light: #60a5fa; --call-orb-dark: #1d4ed8; --call-orb-glow: rgba(96,165,250,0.6); --call-log-you-bg: #1e293b; --call-log-tutor-bg: #14291f; --call-diagram-border: #3f3f3f; --call-face-feature: #0d1533; }
        }
        :root[data-theme="dark"] { --call-orb-light: #60a5fa; --call-orb-dark: #1d4ed8; --call-orb-glow: rgba(96,165,250,0.6); --call-log-you-bg: #1e293b; --call-log-tutor-bg: #14291f; --call-diagram-border: #3f3f3f; --call-face-feature: #0d1533; }
      `}</style>
    </div>
  );
}
