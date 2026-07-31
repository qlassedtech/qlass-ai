import { useEffect, useRef, useState } from "react";
import { api, absoluteUrl, type Teacher, type TeacherAccount } from "../api";

export default function Teachers() {
  const [teachers, setTeachers] = useState<TeacherAccount[]>([]);
  const [me, setMe] = useState<Teacher | null>(null);
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState("teacher");
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [subStatus, setSubStatus] = useState<Record<number, string>>({});
  const [subIsTrial, setSubIsTrial] = useState<Record<number, boolean>>({});
  const [subPaymentRef, setSubPaymentRef] = useState<Record<number, string>>({});
  const fileInputRefs = useRef<Record<number, HTMLInputElement | null>>({});

  function load() {
    api.listTeachers().then(setTeachers);
    api.me().then(setMe);
  }

  useEffect(load, []);

  async function handleActivateTutorPlan(teacherId: number) {
    const isTrial = !!subIsTrial[teacherId];
    const paymentRef = (subPaymentRef[teacherId] || "").trim();
    if (!isTrial && !paymentRef) {
      setSubStatus((prev) => ({ ...prev, [teacherId]: "Payment reference required (or check trial)" }));
      return;
    }
    setSubStatus((prev) => ({ ...prev, [teacherId]: "Activating..." }));
    try {
      const result = await api.activateTeacherTutorSubscription(teacherId, {
        is_trial: isTrial,
        payment_reference: isTrial ? undefined : paymentRef,
      });
      const expires = result.subscription_expires_at ? new Date(result.subscription_expires_at).toLocaleDateString() : "";
      setSubStatus((prev) => ({ ...prev, [teacherId]: `${isTrial ? "Trial activated" : "Activated"} — expires ${expires}` }));
      setSubPaymentRef((prev) => ({ ...prev, [teacherId]: "" }));
    } catch (err) {
      setSubStatus((prev) => ({ ...prev, [teacherId]: err instanceof Error ? err.message : "Failed" }));
    }
  }

  async function handleAdd(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setStatus(null);
    try {
      await api.createTeacher({ name, phone, password, role });
      setName("");
      setPhone("");
      setPassword("");
      setRole("teacher");
      setStatus("Account created!");
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to add teacher");
    }
  }

  async function handlePhotoChange(teacherId: number, e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    await api.uploadTeacherPhoto(teacherId, file);
    load();
  }

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Teacher Accounts</h1>
          <p>Manage who has portal access at your school</p>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 20 }}>
        <h3>Add Teacher</h3>
        <form onSubmit={handleAdd} className="inline-form">
          <input placeholder="Name" value={name} onChange={(e) => setName(e.target.value)} required />
          <input placeholder="WhatsApp number" value={phone} onChange={(e) => setPhone(e.target.value)} required />
          <input
            type="password"
            placeholder="Temporary password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            minLength={6}
          />
          <select value={role} onChange={(e) => setRole(e.target.value)}>
            <option value="teacher">Teacher</option>
            <option value="admin">Admin</option>
          </select>
          <button type="submit">Add</button>
        </form>
        {status && <p className="status">{status}</p>}
        {error && <p className="error">{error}</p>}
      </div>

      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th>Photo</th>
              <th>Name</th>
              <th>Phone</th>
              <th>Role</th>
              {me?.role === "super_admin" && <th>My AI Tutor Plan</th>}
            </tr>
          </thead>
          <tbody>
            {teachers.map((t) => (
              <tr key={t.id}>
                <td>
                  <div
                    className="photo-preview"
                    style={{ width: 40, height: 40, cursor: "pointer" }}
                    onClick={() => fileInputRefs.current[t.id]?.click()}
                  >
                    {t.photo_url ? (
                      <img src={absoluteUrl(t.photo_url) || undefined} alt={t.name} />
                    ) : (
                      <span className="photo-placeholder" style={{ fontSize: 14 }}>
                        {t.name.charAt(0)}
                      </span>
                    )}
                  </div>
                  <input
                    ref={(el) => {
                      fileInputRefs.current[t.id] = el;
                    }}
                    type="file"
                    accept="image/png,image/jpeg,image/webp"
                    style={{ display: "none" }}
                    onChange={(e) => handlePhotoChange(t.id, e)}
                  />
                </td>
                <td>{t.name}</td>
                <td>{t.phone}</td>
                <td style={{ textTransform: "capitalize" }}>{t.role}</td>
                {me?.role === "super_admin" && (
                  <td>
                    <div style={{ display: "flex", flexDirection: "column", gap: 4, minWidth: 180 }}>
                      <label className="toggle-row" style={{ margin: 0, fontSize: 12 }}>
                        <input
                          type="checkbox"
                          checked={!!subIsTrial[t.id]}
                          onChange={(e) => setSubIsTrial((prev) => ({ ...prev, [t.id]: e.target.checked }))}
                        />
                        Free trial
                      </label>
                      {!subIsTrial[t.id] && (
                        <input
                          placeholder="Payment reference"
                          value={subPaymentRef[t.id] || ""}
                          onChange={(e) => setSubPaymentRef((prev) => ({ ...prev, [t.id]: e.target.value }))}
                          style={{ fontSize: 12 }}
                        />
                      )}
                      <button type="button" onClick={() => handleActivateTutorPlan(t.id)}>
                        Activate (₹3500/mo)
                      </button>
                      {subStatus[t.id] && <p className="muted" style={{ fontSize: 12, marginTop: 0 }}>{subStatus[t.id]}</p>}
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
