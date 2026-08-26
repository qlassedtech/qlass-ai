import { useEffect, useRef, useState } from "react";
import { api, absoluteUrl, type School } from "../api";

// Same list used across AssignQuiz/Presentations/StudentDetail/Workbook —
// kept in sync manually since there's no shared constants module yet.
const BOARD_OPTIONS = ["CBSE", "ICSE", "BSEB", "State Board"];

// The bot's real WhatsApp Business number (connected via Wati) — the
// click-to-chat link below opens a chat with this number pre-filled, so a
// student never has to know or type it themselves.
const BOT_WHATSAPP_NUMBER = "917827740390";

// Mirrors app.services.tenancy.slugify_centre_name exactly (lowercase
// letter-runs, joined with hyphens) — a plain character scan rather than a
// regex, same as the backend's own tokenizer. Computed client-side purely
// for display; the backend is the actual source of truth when a
// /join?school=... link is submitted (see tenancy.find_centre_by_slug).
function slugify(name: string): string {
  const words: string[] = [];
  let current = "";
  for (const ch of name.toLowerCase()) {
    if (ch >= "a" && ch <= "z") {
      current += ch;
    } else if (current) {
      words.push(current);
      current = "";
    }
  }
  if (current) words.push(current);
  return words.join("-");
}

export default function SchoolProfile() {
  const [school, setSchool] = useState<School | null>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [linkCopied, setLinkCopied] = useState(false);
  const [waLinkCopied, setWaLinkCopied] = useState(false);
  const [loginLinkCopied, setLoginLinkCopied] = useState(false);
  const [savingApproval, setSavingApproval] = useState(false);
  const [savingBoard, setSavingBoard] = useState(false);

  // Confirmed live (real school onboarding): with no board on file, every
  // student joining through this school's /join link got asked "which
  // board?" in chat — this is the fix for a school that registered before
  // /auth/register-school collected it at signup, or just needs to change it.
  async function handleBoardChange(newBoard: string) {
    if (!school) return;
    setSavingBoard(true);
    try {
      const updated = await api.updateSchool({ board: newBoard });
      setSchool(updated);
    } finally {
      setSavingBoard(false);
    }
  }

  async function toggleAutoApprove() {
    if (!school) return;
    setSavingApproval(true);
    try {
      const updated = await api.updateSchool({ auto_approve_students: !school.auto_approve_students });
      setSchool(updated);
    } finally {
      setSavingApproval(false);
    }
  }
  const fileInputRef = useRef<HTMLInputElement>(null);

  function load() {
    api.getSchool().then(setSchool).catch((err) => setError(err instanceof Error ? err.message : "Failed to load"));
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
      // Layout's sidebar branding is a separate fetch done once on mount
      // (see its own comment) — it has no way to know the logo just
      // changed, and stays mounted across route navigation, so without
      // this it never updates until a hard page reload.
      window.dispatchEvent(new Event("school-branding-updated"));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to upload logo");
    } finally {
      setUploading(false);
    }
  }

  if (!school) return error ? <p className="error">{error}</p> : <p>Loading...</p>;

  const registrationLink = `${window.location.origin}/join?school=${slugify(school.name)}`;
  const loginLink = `${window.location.origin}/login?school=${slugify(school.name)}`;
  const whatsappLink = `https://wa.me/${BOT_WHATSAPP_NUMBER}?text=${encodeURIComponent(
    `Hi, I am a student from ${school.name}. I am excited to get access to AI tutor`,
  )}`;

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
            <span className="logo-placeholder" aria-hidden="true">🏫</span>
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
          <label style={{ marginTop: 16, display: "block", maxWidth: 240 }}>
            Board
            <select value={school.board || ""} onChange={(e) => handleBoardChange(e.target.value)} disabled={savingBoard}>
              <option value="" disabled>
                Not set — students will be asked in chat
              </option>
              {BOARD_OPTIONS.map((b) => (
                <option key={b} value={b}>
                  {b}
                </option>
              ))}
            </select>
          </label>
          {!school.board && (
            <p className="muted" style={{ marginTop: 4, fontSize: 12 }}>
              ⚠️ Every new student joining through your link will be asked which board they're on
              in chat until this is set.
            </p>
          )}
        </div>
      </div>

      <div className="card" style={{ marginTop: 20 }}>
        <h3 style={{ marginTop: 0 }}>Student Registration Link</h3>
        <p className="muted" style={{ marginTop: -8, marginBottom: 16, fontSize: 13 }}>
          Share this with your students — anyone who signs up through it is automatically linked to{" "}
          {school.name}, gets free AI credits, and an activation message on WhatsApp.
        </p>
        <div style={{ display: "flex", gap: 8 }}>
          <input readOnly value={registrationLink} onFocus={(e) => e.target.select()} />
          <button
            type="button"
            style={{ flexShrink: 0 }}
            onClick={() => {
              navigator.clipboard.writeText(registrationLink);
              setLinkCopied(true);
              setTimeout(() => setLinkCopied(false), 2000);
            }}
          >
            {linkCopied ? "Copied!" : "Copy Link"}
          </button>
        </div>
        <label className="toggle-row" style={{ marginTop: 16 }}>
          <input type="checkbox" checked={!school.auto_approve_students} onChange={toggleAutoApprove} disabled={savingApproval} />
          Require a teacher to approve each new student before they join the roster
        </label>
        <p className="muted" style={{ marginTop: 4, fontSize: 12 }}>
          {school.auto_approve_students
            ? "Off — anyone who signs up through the link above joins your roster immediately."
            : "On — new signups appear under \"Pending Approval\" on the Student Roster page until a teacher confirms them. They can still chat with the AI tutor in the meantime."}
        </p>
      </div>

      <div className="card" style={{ marginTop: 20 }}>
        <h3 style={{ marginTop: 0 }}>Sign-In Link</h3>
        <p className="muted" style={{ marginTop: -8, marginBottom: 16, fontSize: 13 }}>
          For someone who already has an account (a teacher, or a student you've already enrolled) — shows{" "}
          {school.name}'s own logo and name instead of the generic Skoolgpt login page. Bookmark-friendly; share this
          instead of the plain site link.
        </p>
        <div style={{ display: "flex", gap: 8 }}>
          <input readOnly value={loginLink} onFocus={(e) => e.target.select()} />
          <button
            type="button"
            style={{ flexShrink: 0 }}
            onClick={() => {
              navigator.clipboard.writeText(loginLink);
              setLoginLinkCopied(true);
              setTimeout(() => setLoginLinkCopied(false), 2000);
            }}
          >
            {loginLinkCopied ? "Copied!" : "Copy Link"}
          </button>
        </div>
      </div>

      <div className="card" style={{ marginTop: 20 }}>
        <h3 style={{ marginTop: 0 }}>WhatsApp Link</h3>
        <p className="muted" style={{ marginTop: -8, marginBottom: 16, fontSize: 13 }}>
          A one-tap alternative to the registration link above — opens WhatsApp directly with a message
          pre-filled, so a student never fills out a form at all. Attributed to {school.name} automatically.
        </p>
        <div style={{ display: "flex", gap: 8 }}>
          <input readOnly value={whatsappLink} onFocus={(e) => e.target.select()} />
          <button
            type="button"
            style={{ flexShrink: 0 }}
            onClick={() => {
              navigator.clipboard.writeText(whatsappLink);
              setWaLinkCopied(true);
              setTimeout(() => setWaLinkCopied(false), 2000);
            }}
          >
            {waLinkCopied ? "Copied!" : "Copy Link"}
          </button>
        </div>
      </div>
    </div>
  );
}
