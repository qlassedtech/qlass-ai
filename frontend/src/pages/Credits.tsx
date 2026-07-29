import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type Student, type Teacher } from "../api";

export default function Credits() {
  const [students, setStudents] = useState<Student[]>([]);
  const [teacher, setTeacher] = useState<Teacher | null>(null);
  const [amounts, setAmounts] = useState<Record<number, string>>({});
  const [reasons, setReasons] = useState<Record<number, "refund" | "goodwill" | "correction">>({});
  const [status, setStatus] = useState<string | null>(null);

  function load() {
    api.listStudents().then(setStudents);
    api.me().then(setTeacher);
  }

  useEffect(load, []);

  async function handleGrant(id: number) {
    setStatus(null);
    const amount = Number(amounts[id]);
    if (!amount) return;
    const reason = reasons[id] || "goodwill";
    try {
      await api.addStudentCredits(id, amount, `Qlass ${reason} credit`, reason);
      setAmounts((prev) => ({ ...prev, [id]: "" }));
      setStatus("Credits added!");
      load();
    } catch (err) {
      setStatus(err instanceof Error ? err.message : "Failed to add credits");
    }
  }

  const payUrl = `${window.location.origin}/pay`;

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Billing & Credits</h1>
          <p>Each student holds their own AI credit wallet</p>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 20 }}>
        <h3>Parent/Student Top-Up Link</h3>
        <p className="muted">
          Share this link with parents or students so they can pay to add credits directly to their child's wallet.
        </p>
        <div className="inline-form">
          <input value={payUrl} readOnly onClick={(e) => (e.target as HTMLInputElement).select()} />
          <button type="button" onClick={() => navigator.clipboard.writeText(payUrl)}>
            Copy Link
          </button>
        </div>
      </div>

      {status && <p className="status">{status}</p>}

      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Phone</th>
              <th>Balance</th>
              {teacher?.role === "super_admin" && <th>Grant Credits</th>}
            </tr>
          </thead>
          <tbody>
            {students.map((s) => (
              <tr key={s.id}>
                <td>
                  <Link to={`/students/${s.id}`}>{s.name}</Link>
                </td>
                <td>{s.phone}</td>
                <td>₹{s.credit_balance.toFixed(2)}</td>
                {teacher?.role === "super_admin" && (
                  <td>
                    <div className="inline-form">
                      <input
                        type="number"
                        step="0.01"
                        placeholder="Amount"
                        value={amounts[s.id] || ""}
                        onChange={(e) => setAmounts((prev) => ({ ...prev, [s.id]: e.target.value }))}
                      />
                      <select
                        value={reasons[s.id] || "goodwill"}
                        onChange={(e) =>
                          setReasons((prev) => ({ ...prev, [s.id]: e.target.value as "refund" | "goodwill" | "correction" }))
                        }
                      >
                        <option value="goodwill">Goodwill</option>
                        <option value="refund">Refund</option>
                        <option value="correction">Correction</option>
                      </select>
                      <button type="button" onClick={() => handleGrant(s.id)}>
                        Grant
                      </button>
                    </div>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
