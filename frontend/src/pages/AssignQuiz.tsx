import { useState } from "react";
import { api } from "../api";

export default function AssignQuiz() {
  const [topic, setTopic] = useState("");
  const [classNum, setClassNum] = useState("");
  const [board, setBoard] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{ assigned_count: number; assigned: string[]; skipped_already_in_quiz: string[] } | null>(
    null,
  );

  async function handleAssign(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setResult(null);
    setLoading(true);
    try {
      const response = await api.assignQuiz({
        topic,
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
            Topic
            <input value={topic} onChange={(e) => setTopic(e.target.value)} placeholder="e.g. circular motion" required />
          </label>
          <label>
            Class (optional — leave blank to target all classes)
            <input value={classNum} onChange={(e) => setClassNum(e.target.value)} placeholder="e.g. 11" />
          </label>
          <label>
            Board (optional)
            <input value={board} onChange={(e) => setBoard(e.target.value)} placeholder="e.g. CBSE" />
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
