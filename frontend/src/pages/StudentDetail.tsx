import { useEffect, useRef, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { api, absoluteUrl, type Student, type ProgressResponse } from "../api";

const FEATURE_KEYS = ["voice", "ocr", "image_generation", "documents", "youtube_videos"] as const;

export default function StudentDetail() {
  const { id } = useParams();
  const studentId = Number(id);
  const [student, setStudent] = useState<Student | null>(null);
  const [progress, setProgress] = useState<ProgressResponse | null>(null);
  const [focusTopic, setFocusTopic] = useState("");
  const [classNum, setClassNum] = useState("");
  const [board, setBoard] = useState("");
  const [parentPhone, setParentPhone] = useState("");
  const [parentName, setParentName] = useState("");
  const [digestPhone, setDigestPhone] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [linkCopied, setLinkCopied] = useState(false);
  const [sendingLink, setSendingLink] = useState(false);
  const [linkStatus, setLinkStatus] = useState<string | null>(null);
  const photoInputRef = useRef<HTMLInputElement>(null);

  function load() {
    api.listStudents().then((all) => {
      const found = all.find((s) => s.id === studentId) || null;
      setStudent(found);
      if (found) {
        setFocusTopic(found.focus_topic || "");
        setClassNum(found.class || "");
        setBoard(found.board || "");
        setParentPhone(found.parent_phone || "");
        setParentName(found.parent_name || "");
      }
    });
    api.getProgress(studentId).then(setProgress);
  }

  useEffect(load, [studentId]);

  async function handleSave() {
    setStatus(null);
    try {
      await api.updateStudent(studentId, {
        class_: classNum, board, focus_topic: focusTopic,
        parent_phone: parentPhone || undefined, parent_name: parentName || undefined,
      });
      setStatus("Saved!");
      load();
    } catch (err) {
      setStatus(err instanceof Error ? err.message : "Failed to save");
    }
  }

  async function toggleFeature(key: string) {
    if (!student) return;
    const next = { ...student.features, [key]: !student.features[key] };
    await api.updateStudent(studentId, { features: next });
    load();
  }

  async function handleSendDigest(e: React.FormEvent) {
    e.preventDefault();
    setStatus(null);
    try {
      await api.sendDigest(studentId, digestPhone);
      setStatus("Digest sent!");
    } catch (err) {
      setStatus(err instanceof Error ? err.message : "Failed to send digest");
    }
  }

  if (!student) return <p>Loading...</p>;

  function buildTopUpLink(): string {
    return `${window.location.origin}/pay?phone=${encodeURIComponent(student!.phone)}`;
  }

  async function copyTopUpLink() {
    if (!student) return;
    const link = buildTopUpLink();
    try {
      await navigator.clipboard.writeText(link);
    } catch {
      // Clipboard API can be blocked (permissions, non-secure context) —
      // fall back to the old textarea+execCommand trick so the action
      // still does something instead of failing silently.
      const textarea = document.createElement("textarea");
      textarea.value = link;
      textarea.style.position = "fixed";
      textarea.style.opacity = "0";
      document.body.appendChild(textarea);
      textarea.select();
      try {
        document.execCommand("copy");
      } catch {
        setLinkStatus(`Couldn't copy automatically — here's the link: ${link}`);
        document.body.removeChild(textarea);
        return;
      }
      document.body.removeChild(textarea);
    }
    setLinkCopied(true);
    setTimeout(() => setLinkCopied(false), 2000);
  }

  async function sendTopUpLinkViaWhatsapp() {
    if (!student) return;
    setLinkStatus(null);
    setSendingLink(true);
    try {
      await api.sendPaymentLink(studentId);
      setLinkStatus(`Sent to ${student.phone} on WhatsApp!`);
    } catch (err) {
      setLinkStatus(err instanceof Error ? err.message : "Failed to send link");
    } finally {
      setSendingLink(false);
    }
  }

  async function handlePhotoChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    await api.uploadStudentPhoto(studentId, file);
    load();
  }

  return (
    <div>
      <Link to="/students">&larr; Back to roster</Link>
      <div style={{ display: "flex", alignItems: "center", gap: 16, marginTop: 12 }}>
        <div className="photo-preview" onClick={() => photoInputRef.current?.click()} style={{ cursor: "pointer" }}>
          {student.photo_url ? (
            <img src={absoluteUrl(student.photo_url) || undefined} alt={student.name} />
          ) : (
            <span className="photo-placeholder">{student.name.charAt(0)}</span>
          )}
        </div>
        <div>
          <h1 style={{ margin: 0 }}>{student.name}</h1>
          <p className="muted" style={{ margin: 0 }}>{student.phone}</p>
        </div>
      </div>
      <input
        ref={photoInputRef}
        type="file"
        accept="image/png,image/jpeg,image/webp"
        style={{ display: "none" }}
        onChange={handlePhotoChange}
      />
      <p className="balance" style={{ marginBottom: 12, marginTop: 16 }}>
        ₹{student.credit_balance.toFixed(2)} <span className="muted" style={{ fontSize: 14, fontWeight: 400 }}>AI credit balance</span>
      </p>
      <div className="inline-form" style={{ marginBottom: 12 }}>
        <button type="button" onClick={copyTopUpLink}>
          {linkCopied ? "Link copied!" : "Copy Top-Up Link"}
        </button>
        <button type="button" onClick={sendTopUpLinkViaWhatsapp} disabled={sendingLink}>
          {sendingLink ? "Sending..." : "Send via WhatsApp"}
        </button>
      </div>
      {linkStatus && <p className="status" style={{ marginBottom: 16 }}>{linkStatus}</p>}

      <div className="grid-2">
        <div className="card">
          <h3>Learner Profile</h3>
          <label>
            Class
            <input value={classNum} onChange={(e) => setClassNum(e.target.value)} />
          </label>
          <label>
            Board
            <input value={board} onChange={(e) => setBoard(e.target.value)} />
          </label>
          <label>
            Focus topic (teacher-assigned)
            <input value={focusTopic} onChange={(e) => setFocusTopic(e.target.value)} placeholder="e.g. quadratic equations" />
          </label>
          <label>
            Parent's WhatsApp number
            <input
              value={parentPhone}
              onChange={(e) => setParentPhone(e.target.value)}
              placeholder="Lets a parent view read-only progress"
            />
          </label>
          <label>
            Parent's name
            <input value={parentName} onChange={(e) => setParentName(e.target.value)} placeholder="Optional" />
          </label>
          <button onClick={handleSave}>Save Changes</button>

          <h3 style={{ marginTop: 28 }}>Feature Access</h3>
          <div className="feature-toggles">
            {FEATURE_KEYS.map((key) => (
              <label key={key} className="toggle-row">
                <input type="checkbox" checked={!!student.features[key]} onChange={() => toggleFeature(key)} />
                {key.replace("_", " ")}
              </label>
            ))}
          </div>

          {status && <p className="status">{status}</p>}
        </div>

        <div className="card">
          <h3>Learning Progress</h3>
          {progress ? (
            <>
              <p>
                {progress.stats.total_evaluated} questions checked,{" "}
                {progress.stats.accuracy_pct !== null ? `${progress.stats.accuracy_pct}% accuracy` : "no data yet"}
              </p>
              <p>Messages exchanged: {progress.stats.messages_sent}</p>
              <p>Current streak: {progress.activity.streak_days} days</p>
              {progress.stats.weak_topics.length > 0 && (
                <p>Needs reinforcement: {progress.stats.weak_topics.join(", ")}</p>
              )}
              {progress.coverage && (
                <p>
                  Syllabus coverage: {progress.coverage.covered.length}/{progress.coverage.total} NCERT chapters
                </p>
              )}
              <p className="muted">
                Hints given: {student.hints_given_count} · Direct solutions: {student.direct_solutions_count}
              </p>
              {student.referral_code && (
                <p className="muted">
                  Referral code: <strong>{student.referral_code}</strong> · Earned via referrals: ₹
                  {student.referral_credits_earned.toFixed(2)}
                </p>
              )}
            </>
          ) : (
            <p>Loading...</p>
          )}

          <h3 style={{ marginTop: 28 }}>Share Progress Report</h3>
          <form onSubmit={handleSendDigest} className="inline-form">
            <input
              placeholder="Teacher or parent's WhatsApp number"
              value={digestPhone}
              onChange={(e) => setDigestPhone(e.target.value)}
              required
            />
            <button type="submit">Send Report</button>
          </form>
        </div>
      </div>
    </div>
  );
}
