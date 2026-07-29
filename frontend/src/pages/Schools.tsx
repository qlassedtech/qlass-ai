import { useEffect, useState } from "react";
import { api, type SchoolOverview } from "../api";

export default function Schools() {
  const [schools, setSchools] = useState<SchoolOverview[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notes, setNotes] = useState<Record<number, string>>({});

  function load() {
    api.getSchoolsOverview().then(setSchools).catch((err) => setError(err instanceof Error ? err.message : "Failed to load"));
  }

  useEffect(load, []);

  async function handleStatusChange(id: number, sales_status: string) {
    try {
      await api.updateSchoolSales(id, { sales_status });
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update status");
    }
  }

  async function handleSaveNotes(id: number) {
    try {
      await api.updateSchoolSales(id, { sales_notes: notes[id] });
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save notes");
    }
  }

  if (error) return <p className="error">{error}</p>;

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Schools & Sales Pipeline</h1>
          <p>Every school on Qlass, with churn risk flagged from real usage</p>
        </div>
      </div>

      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th>School</th>
              <th>Students</th>
              <th>Credit Balance</th>
              <th>Last Activity</th>
              <th>Status</th>
              <th>Notes</th>
            </tr>
          </thead>
          <tbody>
            {schools.map((s) => (
              <tr key={s.id} style={s.is_churn_risk ? { background: "rgba(220, 38, 38, 0.08)" } : undefined}>
                <td>
                  {s.name}
                  {s.is_churn_risk && (
                    <span className="muted" style={{ display: "block", fontSize: 12, color: "#dc2626" }}>
                      ⚠ Churn risk — inactive {s.days_inactive ?? "many"} days
                    </span>
                  )}
                </td>
                <td>{s.student_count}</td>
                <td>₹{s.school_credit_balance.toFixed(2)}</td>
                <td>{s.last_activity ? new Date(s.last_activity).toLocaleDateString() : "Never"}</td>
                <td>
                  <select value={s.sales_status} onChange={(e) => handleStatusChange(s.id, e.target.value)}>
                    <option value="prospect">Prospect</option>
                    <option value="trial">Trial</option>
                    <option value="active">Active</option>
                    <option value="churned">Churned</option>
                  </select>
                </td>
                <td>
                  <div className="inline-form">
                    <input
                      placeholder={s.sales_notes || "Add a note..."}
                      value={notes[s.id] ?? s.sales_notes ?? ""}
                      onChange={(e) => setNotes((prev) => ({ ...prev, [s.id]: e.target.value }))}
                    />
                    <button type="button" onClick={() => handleSaveNotes(s.id)}>
                      Save
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
