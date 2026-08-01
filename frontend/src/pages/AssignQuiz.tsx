import { useEffect, useState } from "react";
import { api } from "../api";

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

export default function AssignQuiz() {
  const [topic, setTopic] = useState("");
  const [classNum, setClassNum] = useState("");
  const [board, setBoard] = useState("");
  const [chapters, setChapters] = useState<Chapter[]>([]);
  const [subject, setSubject] = useState("");
  const [selectedChapterIds, setSelectedChapterIds] = useState<Set<number>>(new Set());
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{ assigned_count: number; assigned: string[]; skipped_already_in_quiz: string[] } | null>(
    null,
  );

  useEffect(() => {
    // The school already knows its own board — default to it instead of a
    // blank "Select a board…" that doesn't correspond to any real
    // curriculum depth/terminology choice. A teacher can still override
    // for a mixed-board school.
    api.getSchool().then((school) => {
      if (school.board) setBoard(school.board);
    });
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

  async function handleAssign(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setResult(null);
    if (!classNum) {
      // A quiz generated with no grade level in mind isn't pitched at
      // anyone properly — the backend rejects this too, but catching it
      // here avoids a round-trip for what's always a real mistake, not an
      // edge case worth silently allowing.
      setError("Please select a class — a quiz needs a grade level to be pitched at the right depth.");
      return;
    }
    if (selectedChapterIds.size === 0 && !topic.trim()) {
      setError("Pick at least one chapter, or type a topic.");
      return;
    }
    setLoading(true);
    try {
      const response = await api.assignQuiz({
        // When chapters are selected, topic is just an optional subtopic
        // focus within them (the backend combines the two) — never
        // required in that case.
        topic: topic.trim() || undefined,
        chapter_ids: selectedChapterIds.size > 0 ? Array.from(selectedChapterIds) : undefined,
        class_: classNum,
        board: board || undefined,
      });
      setResult(response);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to assign quiz");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Assign a Quiz</h1>
          <p>Send a quiz on any topic to a whole class over WhatsApp — auto-graded, no extra work for you.</p>
        </div>
      </div>

      <div className="card" style={{ maxWidth: 480 }}>
        <form onSubmit={handleAssign}>
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
              placeholder={selectedChapterIds.size > 0 ? "e.g. HCF and LCM only" : "e.g. circular motion"}
              required={selectedChapterIds.size === 0}
            />
          </label>
          <button type="submit" disabled={loading}>
            {loading ? "Sending…" : "Assign Quiz"}
          </button>
        </form>

        {error && <p className="error">{error}</p>}

        {result && (
          <div className="status">
            <p>
              Sent to {result.assigned_count} student{result.assigned_count === 1 ? "" : "s"}
              {result.assigned.length > 0 && `: ${result.assigned.join(", ")}`}
            </p>
            {result.skipped_already_in_quiz.length > 0 && (
              <p className="muted">
                Skipped (already mid-quiz): {result.skipped_already_in_quiz.join(", ")}
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
