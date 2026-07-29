import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type Analytics as AnalyticsData } from "../api";

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
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.getAnalytics().then(setData).catch((err) => setError(err instanceof Error ? err.message : "Failed to load"));
  }, []);

  if (error) return <p className="error">{error}</p>;
  if (!data) return <p>Loading...</p>;

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>School Analytics</h1>
          <p>Engagement, progress, and usage across your whole school</p>
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
