import { useEffect, useRef, useState } from "react";
import { api, absoluteUrl, type School } from "../api";

export default function SchoolProfile() {
  const [school, setSchool] = useState<School | null>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  function load() {
    api.getSchool().then(setSchool);
  }

  useEffect(load, []);

  async function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setError(null);
    setUploading(true);
    try {
      await api.uploadSchoolLogo(file);
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to upload logo");
    } finally {
      setUploading(false);
    }
  }

  if (!school) return <p>Loading...</p>;

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>School Profile</h1>
          <p>Your school's branding across the portal and generated documents</p>
        </div>
      </div>

      <div className="card" style={{ display: "flex", alignItems: "center", gap: 24 }}>
        <div className="logo-preview">
          {school.logo_url ? (
            <img src={absoluteUrl(school.logo_url) || undefined} alt={school.name} />
          ) : (
            <span className="logo-placeholder">{school.name.charAt(0)}</span>
          )}
        </div>
        <div style={{ flex: 1 }}>
          <h3 style={{ marginTop: 0 }}>{school.name}</h3>
          {school.city && <p className="muted" style={{ marginTop: -8 }}>{school.city}</p>}
          <input
            ref={fileInputRef}
            type="file"
            accept="image/png,image/jpeg,image/webp"
            style={{ display: "none" }}
            onChange={handleFileChange}
          />
          <button type="button" onClick={() => fileInputRef.current?.click()} disabled={uploading}>
            {uploading ? "Uploading..." : school.logo_url ? "Change Logo" : "Upload Logo"}
          </button>
          {error && <p className="error">{error}</p>}
          <p className="muted" style={{ marginTop: 12, fontSize: 13 }}>
            Shown in your sidebar and stamped on practice-set PDFs generated for your students.
          </p>
        </div>
      </div>
    </div>
  );
}
