import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type Analytics as AnalyticsData, type DeletionRequest, type SchoolOverview, type Teacher } from "../api";

function StatCard({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="card">
      <p className="balance" style={{ fontSize: 28, marginBottom: 4 }}>{value}</p>
      <p className="muted" style={{ fontSize: 13 }}>{label}</p>
    </div>
  );
}

export default function Analytics() {
  const [data, setData] = useState<AnalyticsData | null>(null);
  const [deletionRequests, setDeletionRequests] = useState<DeletionRequest[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [statementDownloading, setStatementDownloading] = useState(false);
  const [me, setMe] = useState<Teacher | null>(null);
  const [schools, setSchools] = useState<SchoolOverview[]>([]);
  const [centreId, setCentreId] = useState("");

  const needsSchoolPicker = me?.role === "org_admin" || me?.role === "super_admin";

  useEffect(() => {
    api.me().then((teacher) => {
      setMe(teacher);
      if (teacher.role === "org_admin" || teacher.role === "super_admin") {
        api.getSchoolsOverview().then(setSchools);
      } else {
        api.getAnalytics().then(setData).catch((err) => setError(err instanceof Error ? err.message : "Failed to load"));
      }
    });
    api.getDeletionRequests().then(setDeletionRequests).catch(() => {});
  }, []);

  useEffect(() => {
    // Analytics is per-school, so org_admin/super_admin only load it once
    // they've picked which of their (potentially many) schools to view.
    if (needsSchoolPicker && centreId) {
      setData(null);
      api
        .getAnalytics(Number(centreId))
        .then(setData)
        .catch((err) => setError(err instanceof Error ? err.message : "Failed to load"));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [centreId]);

  async function handleDownloadStatement() {
    setStatementDownloading(true);
    try {
      const now = new Date();
      const blob = await api.downloadSchoolStatement(
        now.getFullYear(), now.getMonth() + 1, needsSchoolPicker ? Number(centreId) : undefined,
      );
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "school_statement.pdf";
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to download statement");
    } finally {
      setStatementDownloading(false);
    }
  }

  if (error) return <p className="error">{error}</p>;

  if (needsSchoolPicker && !centreId) {
    return (
      <div>
        <div className="page-header">
          <div>
            <h1>School Analytics</h1>
            <p>Pick a school to view its engagement, progress, and usage</p>
          </div>
        </div>
        <div className="card" style={{ maxWidth: 420 }}>
          <label>
            School
            <select value={centreId} onChange={(e) => setCentreId(e.target.value)}>
              <option value="">Select school…</option>
              {schools.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </label>
        </div>
      </div>
    );
  }

  if (!data) return <p>Loading...</p>;

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>School Analytics</h1>
          <p>Engagement, progress, and usage across your whole school</p>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          {needsSchoolPicker && (
            <select value={centreId} onChange={(e) => setCentreId(e.target.value)}>
              {schools.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          )}
          <button type="button" onClick={handleDownloadStatement} disabled={statementDownloading}>
            {statementDownloading ? "Downloading..." : "Download This Month's Statement"}
          </button>
        </div>
      </div>

      <div className="grid-2" style={{ gridTemplateColumns: "repeat(4, 1fr)", marginBottom: 24 }}>
        <StatCard label="Total Students" value={data.total_students} />
        <StatCard label="Active This Week" value={data.active_this_week} />
        <StatCard label="Avg Accuracy" value={data.avg_accuracy_pct !== null ? `${data.avg_accuracy_pct}%` : "—"} />
        <StatCard label="Credit Spend (this month)" value={`₹${data.total_credit_spend_this_month.toFixed(2)}`} />
      </div>

      <div className="grid-2" style={{ marginBottom: 24 }}>
        <div className="card">
          <h3>Teacher Tool Usage This Month</h3>
          <p>Practice worksheets generated: {data.workbook_generations_this_month}</p>
          <p>Presentations generated: {data.presentation_generations_this_month}</p>
        </div>
        <div className="card">
          <h3>Common Weak Topics</h3>
          {data.top_weak_topics.length === 0 ? (
            <p className="muted">No weak-topic data yet</p>
          ) : (
            data.top_weak_topics.map((t) => (
              <p key={t.topic}>
                {t.topic} <span className="muted">({t.incorrect_count} missed)</span>
              </p>
            ))
          )}
        </div>
      </div>

      {data.at_risk_students.length > 0 && (
        <div className="card" style={{ marginBottom: 24 }}>
          <h3>Students At Risk of Falling Behind</h3>
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
              {data.at_risk_students.map((s) => (
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

      {data.upsell_candidates.length > 0 && (
        <div className="card" style={{ marginBottom: 24 }}>
          <h3>Unlimited-Plan Upsell Candidates</h3>
          <p className="muted" style={{ marginBottom: 12 }}>
            These students have hit (or nearly hit) their monthly credit cap — worth pitching the ₹2499/yr unlimited plan.
          </p>
          <table className="data-table" style={{ boxShadow: "none", border: "none" }}>
            <thead>
              <tr>
                <th>Name</th>
                <th>Phone</th>
                <th>Spend This Month</th>
              </tr>
            </thead>
            <tbody>
              {data.upsell_candidates.map((s) => (
                <tr key={s.id}>
                  <td>
                    <Link to={`/students/${s.id}`}>{s.name}</Link>
                  </td>
                  <td>{s.phone}</td>
                  <td>₹{s.spend_this_month.toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {deletionRequests.length > 0 && (
        <div className="card" style={{ marginBottom: 24 }}>
          <h3>Pending Data Deletion Requests</h3>
          <table className="data-table" style={{ boxShadow: "none", border: "none" }}>
            <thead>
              <tr>
                <th>Name</th>
                <th>Phone</th>
                <th>Requested</th>
              </tr>
            </thead>
            <tbody>
              {deletionRequests.map((r) => (
                <tr key={r.id}>
                  <td>{r.name}</td>
                  <td>{r.phone}</td>
                  <td>{new Date(r.requested_at).toLocaleDateString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="muted" style={{ marginTop: 8 }}>
            Contact Skoolgpt support to fulfill a pending deletion request — it's an irreversible action handled by our staff.
          </p>
        </div>
      )}

      <div className="card">
        <h3>Students Needing a Nudge</h3>
        {data.inactive_students.length === 0 ? (
          <p className="muted">Everyone's been active recently — nice!</p>
        ) : (
          <table className="data-table" style={{ boxShadow: "none", border: "none" }}>
            <thead>
              <tr>
                <th>Name</th>
                <th>Phone</th>
                <th>Last Active</th>
              </tr>
            </thead>
            <tbody>
              {data.inactive_students.map((s) => (
                <tr key={s.id}>
                  <td>
                    <Link to={`/students/${s.id}`}>{s.name}</Link>
                  </td>
                  <td>{s.phone}</td>
                  <td>{s.days_since_last_message === null ? "Never messaged" : `${s.days_since_last_message} days ago`}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
