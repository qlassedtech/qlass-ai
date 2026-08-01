import { useEffect, useRef, useState } from "react";
import { api, type School } from "../api";

type Status = "idle" | "generating" | "polling" | "completed" | "failed";

interface Chapter {
  id: number;
  name: string;
  chapter_no: number | null;
  subject: string;
}

export default function Presentations() {
  const [topic, setTopic] = useState("");
  const [classNum, setClassNum] = useState("");
  const [board, setBoard] = useState("");
  const [classOptions, setClassOptions] = useState<string[]>([]);
  const [boardOptions, setBoardOptions] = useState<string[]>([]);
  const [chapters, setChapters] = useState<Chapter[]>([]);
  const [chapterId, setChapterId] = useState("");
  const [numCards, setNumCards] = useState("8");
  const [school, setSchool] = useState<School | null>(null);
  const [status, setStatus] = useState<Status>("idle");
  const [presentationUrl, setPresentationUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    api.getSchool().then(setSchool);
    // Same reasoning as AssignQuiz: populated from the actual student
    // roster rather than free-text entry, so a typo/case mismatch (e.g.
    // "cbse" vs "CBSE") can't silently return an empty chapter list.
    api.listStudents().then((students) => {
      setClassOptions([...new Set(students.map((s) => s.class).filter((c): c is string => !!c))].sort());
      setBoardOptions([...new Set(students.map((s) => s.board).filter((b): b is string => !!b))].sort());
    });
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  useEffect(() => {
    setChapterId("");
    if (!classNum) {
      setChapters([]);
      return;
    }
    api.getCurriculumChapters(classNum, (board || "CBSE").toUpperCase()).then(setChapters);
  }, [classNum, board]);

  async function handleGenerate(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setPresentationUrl(null);
    setStatus("generating");
    try {
      const { generation_id } = await api.generatePresentation({
        topic: chapterId ? undefined : topic,
        chapter_id: chapterId ? Number(chapterId) : undefined,
        class_: classNum || undefined,
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
          <p>Generate a classroom-ready slide deck for any topic (powered by Gamma)</p>
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
              placeholder="e.g. The Water Cycle"
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
