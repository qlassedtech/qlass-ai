import { useState } from "react";
import { api } from "../api";

const FEATURE_KEYS = ["voice", "ocr", "image_generation", "documents", "youtube_videos"] as const;

export default function BulkUpload() {
  const [file, setFile] = useState<File | null>(null);
  const [features, setFeatures] = useState<Record<string, boolean>>({
    voice: false, ocr: false, image_generation: false, documents: false, youtube_videos: false,
  });
  const [result, setResult] = useState<{ created: string[]; updated: string[]; skipped: unknown[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  function toggleFeature(key: string) {
    setFeatures((f) => ({ ...f, [key]: !f[key] }));
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;
    setError(null);
    setResult(null);
    setLoading(true);
    try {
      const res = await api.bulkUploadStudents(file, features);
      setResult(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setLoading(false);
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
      <div className="card" style={{ maxWidth: 540 }}>
        <p className="muted">
          CSV columns: <code>name, phone, class, board, school</code> (board/school/class optional).
          The feature access you select below is applied to every learner in this batch.
        </p>
        <form onSubmit={handleSubmit}>
          <label>
            CSV file
            <input type="file" accept=".csv" onChange={(e) => setFile(e.target.files?.[0] || null)} required />
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

          <button type="submit" disabled={loading || !file} style={{ marginTop: 20 }}>
            {loading ? "Uploading..." : "Upload & Enroll"}
          </button>
        </form>

        {error && <p className="error">{error}</p>}
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
