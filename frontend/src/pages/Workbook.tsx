import { useEffect, useState } from "react";
import { api, type School } from "../api";

export default function Workbook() {
  const [topic, setTopic] = useState("");
  const [classNum, setClassNum] = useState("");
  const [numQuestions, setNumQuestions] = useState("10");
  const [includeAnswerKey, setIncludeAnswerKey] = useState(true);
  const [school, setSchool] = useState<School | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.getSchool().then(setSchool);
  }, []);

  async function handleGenerate(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const blob = await api.generateWorkbook({
        topic,
        class_: classNum || undefined,
        num_questions: Number(numQuestions),
        include_answer_key: includeAnswerKey,
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${topic.replace(/\s+/g, "_")}_worksheet.pdf`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      api.getSchool().then(setSchool);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to generate worksheet");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Practice Worksheet Generator</h1>
          <p>Generate a branded PDF practice set for any topic</p>
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
            <input value={topic} onChange={(e) => setTopic(e.target.value)} placeholder="e.g. Photosynthesis" required />
          </label>
          <label>
            Class (optional)
            <input value={classNum} onChange={(e) => setClassNum(e.target.value)} placeholder="e.g. 8" />
          </label>
          <label>
            Number of questions
            <input
              type="number"
              min={1}
              max={25}
              value={numQuestions}
              onChange={(e) => setNumQuestions(e.target.value)}
            />
          </label>
          <label className="toggle-row">
            <input
              type="checkbox"
              checked={includeAnswerKey}
              onChange={(e) => setIncludeAnswerKey(e.target.checked)}
            />
            Include answer key
          </label>
          <button type="submit" disabled={loading}>
            {loading ? "Generating..." : "Generate & Download PDF"}
          </button>
        </form>
        {error && <p className="error">{error}</p>}
      </div>
    </div>
  );
}
