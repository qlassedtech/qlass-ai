import { useEffect, useRef, useState } from "react";
import { api, absoluteUrl, type SchoolOverview, type Teacher, type TeacherAccount } from "../api";

const TEACHER_COLUMNS = ["name", "phone", "role", "centre_id"] as const;
const SAMPLE_TEACHER_CSV = "name,phone,role,centre_id\nRam Prasad,919000000101,admin,\nSunita Devi,919000000102,teacher,\n";

function downloadSampleTeacherCsv() {
  const blob = new Blob([SAMPLE_TEACHER_CSV], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "sample_teachers.csv";
  a.click();
  URL.revokeObjectURL(url);
}

export default function Teachers() {
  const [teachers, setTeachers] = useState<TeacherAccount[]>([]);
  const [me, setMe] = useState<Teacher | null>(null);
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState("teacher");
  const [schools, setSchools] = useState<SchoolOverview[]>([]);
  const [centreId, setCentreId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [subStatus, setSubStatus] = useState<Record<number, string>>({});
  const [subIsTrial, setSubIsTrial] = useState<Record<number, boolean>>({});
  const [subPaymentRef, setSubPaymentRef] = useState<Record<number, string>>({});
  const [bulkFile, setBulkFile] = useState<File | null>(null);
  const [bulkRows, setBulkRows] = useState<Record<string, string | null>[] | null>(null);
  const [bulkError, setBulkError] = useState<string | null>(null);
  const [bulkResult, setBulkResult] = useState<{ created_count: number; skipped_count: number; generated_passwords: Record<string, string> } | null>(null);
  const [bulkPreviewLoading, setBulkPreviewLoading] = useState(false);
  const [bulkConfirmLoading, setBulkConfirmLoading] = useState(false);
  const fileInputRefs = useRef<Record<number, HTMLInputElement | null>>({});

  function load() {
    api.listTeachers().then(setTeachers);
    api.me().then((teacher) => {
      setMe(teacher);
      // A single-school admin's own centre_id is inferred by the backend —
      // only org_admin/super_admin (who span multiple schools) need to pick
      // which one a new teacher/admin account belongs to.
      if (teacher.role === "org_admin" || teacher.role === "super_admin") {
        api.getSchoolsOverview().then(setSchools);
      }
    });
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

  const needsSchoolPicker = me?.role === "org_admin" || me?.role === "super_admin";

  async function handleAdd(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setStatus(null);
    if (needsSchoolPicker && !centreId) {
      setError("Pick a school for this account");
      return;
    }
    try {
      await api.createTeacher({
        name, phone, password, role,
        centre_id: needsSchoolPicker ? Number(centreId) : undefined,
      });
      setName("");
      setPhone("");
      setPassword("");
      setRole("teacher");
      setCentreId("");
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

  async function handleBulkPreview(e: React.FormEvent) {
    e.preventDefault();
    if (!bulkFile) return;
    setBulkError(null);
    setBulkResult(null);
    setBulkPreviewLoading(true);
    try {
      const res = await api.previewTeacherBulkUpload(bulkFile);
      setBulkRows(
        res.rows.map((r) => ({
          name: r.name ?? "", phone: r.phone ?? "", role: r.role ?? "teacher", centre_id: r.centre_id ?? "",
        })),
      );
    } catch (err) {
      setBulkError(err instanceof Error ? err.message : "Couldn't read that file");
    } finally {
      setBulkPreviewLoading(false);
    }
  }

  function updateBulkCell(index: number, column: string, value: string) {
    setBulkRows((prev) => {
      if (!prev) return prev;
      const next = [...prev];
      next[index] = { ...next[index], [column]: value };
      return next;
    });
  }

  function removeBulkRow(index: number) {
    setBulkRows((prev) => (prev ? prev.filter((_, i) => i !== index) : prev));
  }

  async function handleBulkConfirm() {
    if (!bulkRows || bulkRows.length === 0) return;
    setBulkError(null);
    setBulkConfirmLoading(true);
    try {
      const res = await api.confirmTeacherBulkUpload(bulkRows);
      setBulkResult(res);
      setBulkRows(null);
      setBulkFile(null);
      load();
    } catch (err) {
      setBulkError(err instanceof Error ? err.message : "Bulk upload failed");
    } finally {
      setBulkConfirmLoading(false);
    }
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
          {needsSchoolPicker && (
            <select value={centreId} onChange={(e) => setCentreId(e.target.value)} required>
              <option value="">Select school…</option>
              {schools.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          )}
          <button type="submit">Add</button>
        </form>
        {status && <p className="status">{status}</p>}
        {error && <p className="error">{error}</p>}
      </div>

      {(me?.role === "admin" || me?.role === "org_admin" || me?.role === "super_admin") && (
        <div className="card" style={{ marginBottom: 20 }}>
          <h3>Bulk Upload Teachers/Admins</h3>
          <p className="muted">
            Upload a CSV — columns <code>name, phone, role, centre_id</code> (centre_id required for a
            batch spanning multiple schools; role is teacher or admin — a headmaster/vice-principal is
            just "admin" for their own school) — or a photo, PDF, or Word file of a staff list; the AI
            reads it for you. A blank password is auto-generated and shown after upload so you can share
            it with each new teacher.{" "}
            <button type="button" onClick={downloadSampleTeacherCsv} style={{ padding: "2px 8px", fontSize: 13 }}>
              Download sample CSV
            </button>
          </p>
          {!bulkRows && (
            <form onSubmit={handleBulkPreview}>
              <label>
                File (CSV, image, PDF, or Word)
                <input
                  type="file"
                  accept=".csv,image/*,.pdf,.docx"
                  onChange={(e) => setBulkFile(e.target.files?.[0] || null)}
                  required
                />
              </label>
              <button type="submit" disabled={bulkPreviewLoading || !bulkFile} style={{ marginTop: 12 }}>
                {bulkPreviewLoading ? "Reading file…" : "Preview"}
              </button>
            </form>
          )}

          {bulkError && <p className="error">{bulkError}</p>}

          {bulkRows && (
            <>
              <p className="muted" style={{ marginTop: 16 }}>
                Review and correct anything below before creating accounts — nothing is created yet.
                {bulkRows.length === 0 && " No rows were found in that file."}
              </p>
              <div className="table-scroll">
                <table className="data-table">
                  <thead>
                    <tr>
                      {TEACHER_COLUMNS.map((c) => (
                        <th key={c}>{c}</th>
                      ))}
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {bulkRows.map((row, i) => (
                      <tr key={i}>
                        {TEACHER_COLUMNS.map((c) => (
                          <td key={c}>
                            <input
                              value={row[c] ?? ""}
                              onChange={(e) => updateBulkCell(i, c, e.target.value)}
                              style={{ width: "100%" }}
                            />
                          </td>
                        ))}
                        <td>
                          <button type="button" onClick={() => removeBulkRow(i)}>
                            Remove
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div style={{ marginTop: 16, display: "flex", gap: 12 }}>
                <button type="button" onClick={handleBulkConfirm} disabled={bulkConfirmLoading || bulkRows.length === 0}>
                  {bulkConfirmLoading ? "Creating…" : `Confirm & Create ${bulkRows.length}`}
                </button>
                <button type="button" onClick={() => setBulkRows(null)}>
                  Start Over
                </button>
              </div>
            </>
          )}

          {bulkResult && (
            <div className="status">
              <p>✅ Created: {bulkResult.created_count}</p>
              {bulkResult.skipped_count > 0 && <p>⚠️ Skipped (duplicate phone or missing fields): {bulkResult.skipped_count}</p>}
              {Object.keys(bulkResult.generated_passwords).length > 0 && (
                <div>
                  <p>Generated temporary passwords — share these with each new teacher:</p>
                  <ul>
                    {Object.entries(bulkResult.generated_passwords).map(([phone, pw]) => (
                      <li key={phone}>
                        {phone}: <code>{pw}</code>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </div>
      )}

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
