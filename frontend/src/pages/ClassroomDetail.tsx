import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, errorMessage, type ClassroomDetail as ClassroomDetailData, type Student } from "../api";

function StatCard({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="card">
      <p className="balance" style={{ fontSize: 28, marginBottom: 4 }}>{value}</p>
      <p className="muted" style={{ fontSize: 13 }}>{label}</p>
    </div>
  );
}

export default function ClassroomDetail() {
  const { id } = useParams<{ id: string }>();
  const classroomId = Number(id);

  const [data, setData] = useState<ClassroomDetailData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showAddForm, setShowAddForm] = useState(false);
  const [allStudents, setAllStudents] = useState<Student[]>([]);
  const [search, setSearch] = useState("");
  const [selectedIds, setSelectedIds] = useState<number[]>([]);
  const [busy, setBusy] = useState(false);

  function load() {
    setError(null);
    api
      .getClassroom(classroomId)
      .then(setData)
      .catch((err) => setError(errorMessage(err, "Failed to load classroom")));
  }

  useEffect(load, [classroomId]);

  function openAddForm() {
    setShowAddForm(true);
    setSelectedIds([]);
    setSearch("");
    api.fetchAllStudents().then(setAllStudents).catch(() => {});
  }

  const rosterIds = useMemo(() => new Set((data?.students ?? []).map((s) => s.id)), [data]);

  const searchResults = useMemo(() => {
    if (!showAddForm) return [];
    const q = search.trim().toLowerCase();
    return allStudents
      .filter((s) => !rosterIds.has(s.id))
      .filter((s) => !q || s.name.toLowerCase().includes(q) || s.phone.includes(q))
      .slice(0, 50);
  }, [allStudents, rosterIds, search, showAddForm]);

  function toggleSelected(studentId: number) {
    setSelectedIds((ids) => (ids.includes(studentId) ? ids.filter((i) => i !== studentId) : [...ids, studentId]));
  }

  async function handleAddStudents() {
    if (selectedIds.length === 0) return;
    setBusy(true);
    try {
      await api.assignClassroomStudents(classroomId, selectedIds);
      setShowAddForm(false);
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to add students");
    } finally {
      setBusy(false);
    }
  }

  async function handleRemove(studentId: number) {
    setBusy(true);
    try {
      await api.unassignClassroomStudent(classroomId, studentId);
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to remove student");
    } finally {
      setBusy(false);
    }
  }

  if (error && !data) return <p className="error">{error}</p>;
  if (!data) return <p>Loading...</p>;

  const { classroom, students, analytics } = data;

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>{classroom.name}</h1>
          <p>
            {[classroom.class && `Class ${classroom.class}`, classroom.board, classroom.subject].filter(Boolean).join(" · ") ||
              "Classroom roster and progress"}
          </p>
        </div>
        <button onClick={openAddForm}>+ Add Students</button>
      </div>

      {error && <p className="error">{error}</p>}

      {showAddForm && (
        <div className="card" style={{ marginBottom: 24 }}>
          <h3 style={{ marginTop: 0 }}>Add students to this classroom</h3>
          <input
            placeholder="Search by name or phone"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{ marginBottom: 12 }}
          />
          <div className="table-scroll" style={{ maxHeight: 260 }}>
            <table className="data-table" style={{ boxShadow: "none", border: "none" }}>
              <thead>
                <tr>
                  <th></th>
                  <th>Name</th>
                  <th>Phone</th>
                  <th>Class</th>
                </tr>
              </thead>
              <tbody>
                {searchResults.map((s) => (
                  <tr key={s.id}>
                    <td>
                      <input type="checkbox" checked={selectedIds.includes(s.id)} onChange={() => toggleSelected(s.id)} />
                    </td>
                    <td>{s.name}</td>
                    <td>{s.phone}</td>
                    <td>{s.class || "—"}</td>
                  </tr>
                ))}
                {searchResults.length === 0 && (
                  <tr>
                    <td colSpan={4} className="muted">
                      No matching students
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
            <button type="button" onClick={handleAddStudents} disabled={busy || selectedIds.length === 0}>
              {busy ? "Adding..." : `Add ${selectedIds.length || ""} Student${selectedIds.length === 1 ? "" : "s"}`}
            </button>
            <button type="button" onClick={() => setShowAddForm(false)}>
              Cancel
            </button>
          </div>
        </div>
      )}

      <div className="grid-2" style={{ gridTemplateColumns: "repeat(4, 1fr)", marginBottom: 24 }}>
        <StatCard label="Students" value={analytics.total_students} />
        <StatCard label="Active This Week" value={analytics.active_this_week} />
        <StatCard label="Avg Accuracy" value={analytics.avg_accuracy_pct !== null ? `${analytics.avg_accuracy_pct}%` : "—"} />
        <StatCard label="At Risk" value={analytics.at_risk_students.length} />
      </div>

      <div className="grid-2" style={{ marginBottom: 24 }}>
        <div className="card">
          <h3>Common Weak Topics</h3>
          {analytics.top_weak_topics.length === 0 ? (
            <p className="muted">No weak-topic data yet</p>
          ) : (
            analytics.top_weak_topics.map((t) => (
              <p key={t.topic}>
                {t.topic} <span className="muted">({t.incorrect_count} missed)</span>
              </p>
            ))
          )}
        </div>
        <div className="card">
          <h3>Inactive Students</h3>
          {analytics.inactive_students.length === 0 ? (
            <p className="muted">Everyone's been active recently — nice!</p>
          ) : (
            analytics.inactive_students.map((s) => (
              <p key={s.id}>
                <Link to={`/students/${s.id}`}>{s.name}</Link>{" "}
                <span className="muted">
                  {s.days_since_last_message === null ? "(never messaged)" : `(${s.days_since_last_message} days ago)`}
                </span>
              </p>
            ))
          )}
        </div>
      </div>

      {analytics.at_risk_students.length > 0 && (
        <div className="card" style={{ marginBottom: 24 }}>
          <h3>Students Who Need Help</h3>
          <p className="muted" style={{ marginBottom: 12 }}>
            Poor recent accuracy or currently stuck on hints without solving — worth a check-in.
          </p>
          <table className="data-table" style={{ boxShadow: "none", border: "none" }}>
            <thead>
              <tr>
                <th>Name</th>
                <th>Phone</th>
                <th>Accuracy</th>
                <th>Consecutive Unresolved Hints</th>
              </tr>
            </thead>
            <tbody>
              {analytics.at_risk_students.map((s) => (
                <tr key={s.id}>
                  <td>
                    <Link to={`/students/${s.id}`}>{s.name}</Link>
                  </td>
                  <td>{s.phone}</td>
                  <td>{s.accuracy_pct !== null ? `${s.accuracy_pct}%` : "—"}</td>
                  <td>{s.consecutive_unresolved_hints}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="card">
        <h3 style={{ marginTop: 0 }}>Roster ({students.length})</h3>
        <div className="table-scroll">
          <table className="data-table" style={{ boxShadow: "none", border: "none" }}>
            <thead>
              <tr>
                <th>Name</th>
                <th>Phone</th>
                <th>Class</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {students.map((s) => (
                <tr key={s.id}>
                  <td>
                    <Link to={`/students/${s.id}`}>{s.name}</Link>
                  </td>
                  <td>{s.phone}</td>
                  <td>{s.class || "—"}</td>
                  <td>
                    <button type="button" onClick={() => handleRemove(s.id)} disabled={busy}>
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
              {students.length === 0 && (
                <tr>
                  <td colSpan={4} className="muted">
                    No students in this classroom yet — use "+ Add Students" above.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
