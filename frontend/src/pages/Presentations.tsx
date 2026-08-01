import { useEffect, useRef, useState } from "react";
import { api, type School } from "../api";

type Status = "idle" | "generating" | "polling" | "completed" | "failed";

interface Chapter {
  id: number;
  name: string;
  chapter_no: number | null;
  subject: string;
}

// Fixed, universal choices — NOT derived from the existing student roster.
// Deriving from the roster was a real bug: a brand-new school (or one
// whose students haven't had class/board filled in yet) had nothing to
// populate these dropdowns with, so both silently showed no options at
// all, which then also blocked the Chapter picker further down (it only
// loads once a class is picked). A teacher must be able to target ANY
// class/board here, including ones no student is enrolled in yet.
const CLASS_OPTIONS = Array.from({ length: 12 }, (_, i) => String(i + 1));
const BOARD_OPTIONS = ["CBSE", "ICSE", "BSEB", "State Board"];

export default function Presentations() {
  const [topic, setTopic] = useState("");
  const [classNum, setClassNum] = useState("");
  const [board, setBoard] = useState("");
  const [chapters, setChapters] = useState<Chapter[]>([]);
  const [subject, setSubject] = useState("");
  const [selectedChapterIds, setSelectedChapterIds] = useState<Set<number>>(new Set());
  const [numCards, setNumCards] = useState("8");
  const [school, setSchool] = useState<School | null>(null);
  const [status, setStatus] = useState<Status>("idle");
  const [presentationUrl, setPresentationUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    api.getSchool().then((s) => {
      setSchool(s);
      // The school already knows its own board — default to it instead of
      // a blank "Select a board…" that doesn't correspond to any real
      // curriculum depth/terminology choice. A teacher can still override
      // for a mixed-board school.
      if (s.board) setBoard(s.board);
    });
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  useEffect(() => {
    // The seeded curriculum is keyed by class AND board (e.g. CBSE/NCERT vs
    // BSEB have different chapter lists for the same class) — only fetch
    // once a specific class is picked. Defaults to CBSE when no board is
    // set, since that's the larger seeded curriculum.
    setSubject("");
    setSelectedChapterIds(new Set());
    if (!classNum) {
      setChapters([]);
      return;
    }
    api.getCurriculumChapters(classNum, (board || "CBSE").toUpperCase()).then(setChapters);
  }, [classNum, board]);

  const subjects = [...new Set(chapters.map((c) => c.subject))].sort();
  const chaptersForSubject = chapters.filter((c) => c.subject === subject);

  function toggleChapter(id: number) {
    setSelectedChapterIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function handleGenerate(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setPresentationUrl(null);
    if (!classNum) {
      // A deck pitched at no grade level in particular comes out generic —
      // exactly the "classroom-ready" promise this page makes falls apart
      // without it, so this is caught here rather than silently allowed.
      setError("Please select a class — a presentation needs a grade level to be pitched at the right depth.");
      return;
    }
    if (selectedChapterIds.size === 0 && !topic.trim()) {
      setError("Pick at least one chapter, or type a topic.");
      return;
    }
    setStatus("generating");
    try {
      const { generation_id } = await api.generatePresentation({
        // When chapters are selected, topic is just an optional subtopic
        // focus within them (the backend combines the two) — never
        // required in that case.
        topic: topic.trim() || undefined,
        chapter_ids: selectedChapterIds.size > 0 ? Array.from(selectedChapterIds) : undefined,
        class_: classNum,
        board: board || undefined,
        num_cards: Number(numCards),
      });
      setStatus("polling");
      pollRef.current = setInterval(async () => {
        try {
          const result = await api.getPresentationStatus(generation_id);
          if (result.status === "completed") {
            setStatus("completed");
            setPresentationUrl(result.url);
            api.getSchool().then(setSchool);
            if (pollRef.current) clearInterval(pollRef.current);
          } else if (result.status === "failed") {
            setStatus("failed");
            setError("Presentation generation failed — please try again");
            if (pollRef.current) clearInterval(pollRef.current);
          }
        } catch (err) {
          setStatus("failed");
          setError(err instanceof Error ? err.message : "Failed to check status");
          if (pollRef.current) clearInterval(pollRef.current);
        }
      }, 3000);
    } catch (err) {
      setStatus("failed");
      setError(err instanceof Error ? err.message : "Failed to start presentation generation");
    }
  }

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Presentation Generator</h1>
          <p>Generate a classroom-ready slide deck for any topic</p>
        </div>
      </div>

      {school && (
        <p className="muted" style={{ marginBottom: 16 }}>
          School credit balance: ₹{school.credit_balance?.toFixed(2)}
        </p>
      )}

      <div className="card" style={{ maxWidth: 480 }}>
        <form onSubmit={handleGenerate}>
          <label>
            Class
            <select value={classNum} onChange={(e) => setClassNum(e.target.value)} required>
              <option value="">Select a class…</option>
              {CLASS_OPTIONS.map((c) => (
                <option key={c} value={c}>
                  Class {c}
                </option>
              ))}
            </select>
          </label>

          <label>
            Board
            <select value={board} onChange={(e) => setBoard(e.target.value)}>
              <option value="">Select a board…</option>
              {BOARD_OPTIONS.map((b) => (
                <option key={b} value={b}>
                  {b}
                </option>
              ))}
            </select>
          </label>

          {subjects.length > 0 && (
            <label>
              Subject
              <select
                value={subject}
                onChange={(e) => {
                  setSubject(e.target.value);
                  setSelectedChapterIds(new Set());
                }}
              >
                <option value="">Select a subject…</option>
                {subjects.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </label>
          )}

          {chaptersForSubject.length > 0 && (
            <div style={{ marginBottom: 16 }}>
              <p className="muted" style={{ fontSize: 13, fontWeight: 600, marginBottom: 6 }}>
                Chapters (pick one or more — optional, or type a topic below)
              </p>
              <div style={{ maxHeight: 220, overflowY: "auto", border: "1px solid var(--border)", borderRadius: 10, padding: 8 }}>
                {chaptersForSubject.map((c) => (
                  <label key={c.id} className="toggle-row">
                    <input
                      type="checkbox"
                      checked={selectedChapterIds.has(c.id)}
                      onChange={() => toggleChapter(c.id)}
                    />
                    {c.chapter_no ? `Ch. ${c.chapter_no} — ` : ""}
                    {c.name}
                  </label>
                ))}
              </div>
            </div>
          )}

          <label>
            {selectedChapterIds.size > 0 ? "Focus within selected chapters (optional)" : "Topic"}
            <input
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              placeholder={selectedChapterIds.size > 0 ? "e.g. just HCF and LCM" : "e.g. The Water Cycle"}
              required={selectedChapterIds.size === 0}
            />
          </label>
          <label>
            Number of slides
            <input type="number" min={3} max={20} value={numCards} onChange={(e) => setNumCards(e.target.value)} />
          </label>
          <button type="submit" disabled={status === "generating" || status === "polling"}>
            {status === "generating" && "Starting..."}
            {status === "polling" && "Generating slides..."}
            {(status === "idle" || status === "completed" || status === "failed") && "Generate Presentation"}
          </button>
        </form>
        {error && <p className="error">{error}</p>}
        {status === "completed" && presentationUrl && (
          <p className="status">
            Your presentation is ready —{" "}
            <a href={presentationUrl} target="_blank" rel="noreferrer">
              open it here
            </a>
          </p>
        )}
      </div>
    </div>
  );
}
