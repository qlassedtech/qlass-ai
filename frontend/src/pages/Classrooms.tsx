import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, errorMessage, type Classroom } from "../api";

export default function Classrooms() {
  const [classrooms, setClassrooms] = useState<Classroom[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState("");
  const [classNum, setClassNum] = useState("");
  const [board, setBoard] = useState("");
  const [subject, setSubject] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  function load() {
    setLoading(true);
    api
      .listClassrooms()
      .then(setClassrooms)
      .catch((err) => setError(errorMessage(err, "Failed to load classrooms")))
      .finally(() => setLoading(false));
  }

  useEffect(load, []);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setCreating(true);
    try {
      await api.createClassroom({
        name, class_: classNum || undefined, board: board || undefined, subject: subject || undefined,
      });
      setName("");
      setClassNum("");
      setBoard("");
      setSubject("");
      setShowForm(false);
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create classroom");
    } finally {
      setCreating(false);
    }
  }

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Classrooms</h1>
          <p>Group your students into cohorts to track who needs help</p>
        </div>
        <button onClick={() => setShowForm((v) => !v)}>{showForm ? "Cancel" : "+ Create Classroom"}</button>
      </div>

      {error && !showForm && <p className="error">{error}</p>}

      {showForm && (
        <form className="card inline-form" onSubmit={handleCreate} style={{ marginBottom: 24 }}>
          <input placeholder="Classroom name (e.g. Class 10A Physics)" value={name} onChange={(e) => setName(e.target.value)} required />
          <input placeholder="Class" value={classNum} onChange={(e) => setClassNum(e.target.value)} />
          <input placeholder="Board (CBSE/ICSE/State)" value={board} onChange={(e) => setBoard(e.target.value)} />
          <input placeholder="Subject" value={subject} onChange={(e) => setSubject(e.target.value)} />
          <button type="submit" disabled={creating}>{creating ? "Creating..." : "Create"}</button>
          {error && <p className="error">{error}</p>}
        </form>
      )}

      {loading ? (
        <p>Loading...</p>
      ) : classrooms.length === 0 ? (
        <div className="card">
          <p className="muted">No classrooms yet — create one to start grouping students into a cohort you can track progress for.</p>
        </div>
      ) : (
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Class</th>
                <th>Board</th>
                <th>Subject</th>
                <th>Students</th>
              </tr>
            </thead>
            <tbody>
              {classrooms.map((c) => (
                <tr key={c.id}>
                  <td>
                    <Link to={`/classrooms/${c.id}`}>{c.name}</Link>
                  </td>
                  <td>{c.class || "—"}</td>
                  <td>{c.board || "—"}</td>
                  <td>{c.subject || "—"}</td>
                  <td>{c.student_count ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
