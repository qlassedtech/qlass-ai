import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type Student } from "../api";

export default function StudentList() {
  const [students, setStudents] = useState<Student[]>([]);
  const [pending, setPending] = useState<Student[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [classNum, setClassNum] = useState("");
  const [board, setBoard] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [approvingId, setApprovingId] = useState<number | null>(null);

  function load() {
    setLoading(true);
    Promise.all([api.fetchAllStudents(), api.listPendingStudents()])
      .then(([all, pendingList]) => {
        setStudents(all);
        setPending(pendingList);
      })
      .finally(() => setLoading(false));
  }

  useEffect(load, []);

  async function handleApprove(id: number) {
    setApprovingId(id);
    try {
      await api.approveStudent(id);
      load();
    } finally {
      setApprovingId(null);
    }
  }

  async function handleAdd(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await api.createStudent({ name, phone, class_: classNum || undefined, board: board || undefined });
      setName("");
      setPhone("");
      setClassNum("");
      setBoard("");
      setShowForm(false);
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to add student");
    }
  }

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Student Roster</h1>
          <p>Every learner enrolled under your account</p>
        </div>
        <button onClick={() => setShowForm((v) => !v)}>{showForm ? "Cancel" : "+ Enroll Student"}</button>
      </div>

      {pending.length > 0 && (
        <div className="card" style={{ marginBottom: 24, borderColor: "var(--warning, #d97706)" }}>
          <h3 style={{ marginTop: 0 }}>Pending Approval ({pending.length})</h3>
          <p className="muted" style={{ marginTop: -8, marginBottom: 16, fontSize: 13 }}>
            Signed up through your school's own registration link, not yet confirmed as one of your students.
            They can already chat with the AI tutor — approving just moves them into your main roster.
          </p>
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Phone</th>
                  <th>Class</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {pending.map((s) => (
                  <tr key={s.id}>
                    <td>
                      <Link to={`/students/${s.id}`}>{s.name}</Link>
                    </td>
                    <td>{s.phone}</td>
                    <td>{s.class || "—"}</td>
                    <td>
                      <button type="button" onClick={() => handleApprove(s.id)} disabled={approvingId === s.id}>
                        {approvingId === s.id ? "Approving..." : "Approve"}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {showForm && (
        <form className="card inline-form" onSubmit={handleAdd} style={{ marginBottom: 24 }}>
          <input placeholder="Full name" value={name} onChange={(e) => setName(e.target.value)} required />
          <input placeholder="WhatsApp number (91XXXXXXXXXX)" value={phone} onChange={(e) => setPhone(e.target.value)} required />
          <input placeholder="Class" value={classNum} onChange={(e) => setClassNum(e.target.value)} />
          <input placeholder="Board (CBSE/ICSE/State)" value={board} onChange={(e) => setBoard(e.target.value)} />
          <button type="submit">Enroll</button>
          {error && <p className="error">{error}</p>}
        </form>
      )}

      {loading ? (
        <p>Loading...</p>
      ) : (
        <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Phone</th>
              <th>Class</th>
              <th>Board</th>
              <th>Focus topic</th>
              <th>Features</th>
            </tr>
          </thead>
          <tbody>
            {students.map((s) => (
              <tr key={s.id}>
                <td>
                  <Link to={`/students/${s.id}`}>{s.name}</Link>
                  {s.approval_status === "pending" && (
                    <span className="muted" style={{ fontSize: 11, marginLeft: 6 }}>
                      (pending approval)
                    </span>
                  )}
                </td>
                <td>{s.phone}</td>
                <td>{s.class || "—"}</td>
                <td>{s.board || "—"}</td>
                <td>{s.focus_topic || "—"}</td>
                <td>
                  {Object.entries(s.features)
                    .filter(([, v]) => v)
                    .map(([k]) => k)
                    .join(", ") || "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      )}
    </div>
  );
}
