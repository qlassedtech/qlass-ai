import { useEffect, useState } from "react";
import { api, type SchoolOverview, type Teacher } from "../api";

const FEATURE_KEYS = ["voice", "ocr", "image_generation", "documents", "youtube_videos"] as const;
const COLUMNS = ["name", "phone", "class", "board", "school"] as const;

const SAMPLE_CSV = "name,phone,class,board,school\nAman Kumar,919000000001,10,BSEB,Patna High School\nSunita Devi,919000000002,9,CBSE,\n";

function downloadSampleCsv() {
  const blob = new Blob([SAMPLE_CSV], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "sample_students.csv";
  a.click();
  URL.revokeObjectURL(url);
}

export default function BulkUpload() {
  const [file, setFile] = useState<File | null>(null);
  const [features, setFeatures] = useState<Record<string, boolean>>({
    voice: false, ocr: false, image_generation: false, documents: false, youtube_videos: false,
  });
  const [rows, setRows] = useState<Record<string, string | null>[] | null>(null);
  const [result, setResult] = useState<{ created: string[]; updated: string[]; skipped: unknown[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [confirmLoading, setConfirmLoading] = useState(false);
  const [me, setMe] = useState<Teacher | null>(null);
  const [schools, setSchools] = useState<SchoolOverview[]>([]);
  const [centreId, setCentreId] = useState("");

  useEffect(() => {
    api.me().then((teacher) => {
      setMe(teacher);
      // A single-school admin's own centre_id is inferred by the backend —
      // only org_admin/super_admin (who span multiple schools) need to pick
      // which school this batch is enrolling into (see admin.py's
      // _resolve_centre_for_write; Student.centre_id is nullable, so
      // skipping this for those roles used to silently create students
      // belonging to no school at all).
      if (teacher.role === "org_admin" || teacher.role === "super_admin") {
        api.getSchoolsOverview().then(setSchools);
      }
    });
  }, []);

  const needsSchoolPicker = me?.role === "org_admin" || me?.role === "super_admin";

  function toggleFeature(key: string) {
    setFeatures((f) => ({ ...f, [key]: !f[key] }));
  }

  async function handlePreview(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;
    if (needsSchoolPicker && !centreId) {
      setError("Pick a school for this batch");
      return;
    }
    setError(null);
    setResult(null);
    setRows(null);
    setPreviewLoading(true);
    try {
      const res = await api.previewStudentBulkUpload(file, needsSchoolPicker ? Number(centreId) : undefined);
      setRows(res.rows.map((r) => ({ name: r.name ?? "", phone: r.phone ?? "", class: r.class ?? "", board: r.board ?? "", school: r.school ?? "" })));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't read that file");
    } finally {
      setPreviewLoading(false);
    }
  }

  function updateCell(index: number, column: string, value: string) {
    setRows((prev) => {
      if (!prev) return prev;
      const next = [...prev];
      next[index] = { ...next[index], [column]: value };
      return next;
    });
  }

  function removeRow(index: number) {
    setRows((prev) => (prev ? prev.filter((_, i) => i !== index) : prev));
  }

  async function handleConfirm() {
    if (!rows || rows.length === 0) return;
    setError(null);
    setConfirmLoading(true);
    try {
      const res = await api.confirmStudentBulkUpload(rows, features, needsSchoolPicker ? Number(centreId) : undefined);
      setResult(res);
      setRows(null);
      setFile(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Enrollment failed");
    } finally {
      setConfirmLoading(false);
    }
  }

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Bulk Enrollment</h1>
          <p>Enroll an entire class or cohort in one upload</p>
        </div>
      </div>
      <div className="card" style={{ maxWidth: 720 }}>
        <p className="muted">
          Upload a CSV — columns <code>name, phone, class, board, school</code> (board/school/class
          optional) — or a photo, PDF, or Word file of a roster/register page; the AI reads it for
          you.{" "}
          <button type="button" onClick={downloadSampleCsv} style={{ padding: "2px 8px", fontSize: 13 }}>
            Download sample CSV
          </button>
        </p>
        {!rows && (
          <form onSubmit={handlePreview}>
            {needsSchoolPicker && (
              <label>
                School
                <select value={centreId} onChange={(e) => setCentreId(e.target.value)} required>
                  <option value="">Select school…</option>
                  {schools.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <label>
              File (CSV, image, PDF, or Word)
              <input
                type="file"
                accept=".csv,image/*,.pdf,.docx"
                onChange={(e) => setFile(e.target.files?.[0] || null)}
                required
              />
            </label>

            <h3 style={{ marginTop: 20 }}>Feature access for this batch</h3>
            <div className="feature-toggles">
              {FEATURE_KEYS.map((key) => (
                <label key={key} className="toggle-row">
                  <input type="checkbox" checked={features[key]} onChange={() => toggleFeature(key)} />
                  {key.replace("_", " ")}
                </label>
              ))}
            </div>

            <button type="submit" disabled={previewLoading || !file} style={{ marginTop: 20 }}>
              {previewLoading ? "Reading file…" : "Preview"}
            </button>
          </form>
        )}

        {error && <p className="error">{error}</p>}

        {rows && (
          <>
            <p className="muted" style={{ marginTop: 16 }}>
              Review and correct anything below before enrolling — nothing is created yet.
              {rows.length === 0 && " No rows were found in that file."}
            </p>
            <div className="table-scroll">
              <table className="data-table">
                <thead>
                  <tr>
                    {COLUMNS.map((c) => (
                      <th key={c}>{c}</th>
                    ))}
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row, i) => (
                    <tr key={i}>
                      {COLUMNS.map((c) => (
                        <td key={c}>
                          <input
                            value={row[c] ?? ""}
                            onChange={(e) => updateCell(i, c, e.target.value)}
                            style={{ width: "100%" }}
                          />
                        </td>
                      ))}
                      <td>
                        <button type="button" onClick={() => removeRow(i)}>
                          Remove
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div style={{ marginTop: 16, display: "flex", gap: 12 }}>
              <button type="button" onClick={handleConfirm} disabled={confirmLoading || rows.length === 0}>
                {confirmLoading ? "Enrolling…" : `Confirm & Enroll ${rows.length}`}
              </button>
              <button type="button" onClick={() => setRows(null)}>
                Start Over
              </button>
            </div>
          </>
        )}

        {result && (
          <div className="status">
            <p>✅ Enrolled: {result.created.length}</p>
            <p>🔄 Updated: {result.updated.length}</p>
            {result.skipped.length > 0 && <p>⚠️ Skipped (missing name/phone): {result.skipped.length}</p>}
          </div>
        )}
      </div>
    </div>
  );
}
