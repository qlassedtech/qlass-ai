import { useEffect, useRef, useState } from "react";
import { api, type School } from "../api";

type Status = "idle" | "generating" | "polling" | "completed" | "failed";

export default function Presentations() {
  const [topic, setTopic] = useState("");
  const [numCards, setNumCards] = useState("8");
  const [school, setSchool] = useState<School | null>(null);
  const [status, setStatus] = useState<Status>("idle");
  const [presentationUrl, setPresentationUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    api.getSchool().then(setSchool);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  async function handleGenerate(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setPresentationUrl(null);
    setStatus("generating");
    try {
      const { generation_id } = await api.generatePresentation({ topic, num_cards: Number(numCards) });
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

      <div className="card">
        <form onSubmit={handleGenerate}>
          <label>
            Topic
            <input value={topic} onChange={(e) => setTopic(e.target.value)} placeholder="e.g. The Water Cycle" required />
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
