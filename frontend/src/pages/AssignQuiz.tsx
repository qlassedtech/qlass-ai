import { useEffect, useState } from "react";
import { api } from "../api";

interface Chapter {
  id: number;
  name: string;
  chapter_no: number | null;
  subject: string;
}

export default function AssignQuiz() {
  const [topic, setTopic] = useState("");
  const [classNum, setClassNum] = useState("");
  const [board, setBoard] = useState("");
  const [classOptions, setClassOptions] = useState<string[]>([]);
  const [boardOptions, setBoardOptions] = useState<string[]>([]);
  const [chapters, setChapters] = useState<Chapter[]>([]);
  const [chapterId, setChapterId] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{ assigned_count: number; assigned: string[]; skipped_already_in_quiz: string[] } | null>(
    null,
  );

  useEffect(() => {
    // Populated from the actual student roster rather than free-text entry
    // — a typo or case mismatch (e.g. "cbse" vs "CBSE") would silently
    // return "No matching students found," since the backend filters by
    // exact match.
    api.listStudents().then((students) => {
      setClassOptions([...new Set(students.map((s) => s.class).filter((c): c is string => !!c))].sort());
      setBoardOptions([...new Set(students.map((s) => s.board).filter((b): b is string => !!b))].sort());
    });
  }, []);

  useEffect(() => {
    // The seeded curriculum is keyed by class AND board (e.g. CBSE/NCERT vs
    // BSEB have different chapter lists for the same class) — only fetch
    // once a specific class is picked (chapters don't make sense for "all
    // classes"). Defaults to CBSE when no board filter is selected, since
    // that's the larger seeded curriculum.
    setChapterId("");
    if (!classNum) {
      setChapters([]);
      return;
    }
    api.getCurriculumChapters(classNum, (board || "CBSE").toUpperCase()).then(setChapters);
  }, [classNum, board]);

  async function handleAssign(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setResult(null);
    setLoading(true);
    try {
      const response = await api.assignQuiz({
        topic: chapterId ? undefined : topic,
        chapter_id: chapterId ? Number(chapterId) : undefined,
        class_: classNum || undefined,
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
            <select value={classNum} onChange={(e) => setClassNum(e.target.value)}>
              <option value="">All classes</option>
              {classOptions.map((c) => (
                <option key={c} value={c}>
                  Class {c}
                </option>
              ))}
            </select>
          </label>

          {chapters.length > 0 && (
            <label>
              Chapter (optional — from the curriculum for this class and board)
              <select value={chapterId} onChange={(e) => setChapterId(e.target.value)}>
                <option value="">— pick a chapter, or type a topic below —</option>
                {chapters.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.subject}: {c.chapter_no ? `Ch. ${c.chapter_no} — ` : ""}
                    {c.name}
                  </option>
                ))}
              </select>
            </label>
          )}

          <label>
            Topic {chapterId && <span className="muted">(ignored — a chapter is selected above)</span>}
            <input
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              placeholder="e.g. circular motion"
              disabled={!!chapterId}
              required={!chapterId}
            />
          </label>
          <label>
            Board
            <select value={board} onChange={(e) => setBoard(e.target.value)}>
              <option value="">All boards</option>
              {boardOptions.map((b) => (
                <option key={b} value={b}>
                  {b}
                </option>
              ))}
            </select>
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
