import { useEffect, useState } from "react";
import ParentLayout from "../components/ParentLayout";
import { parentApi, type ParentProfile, type ParentProgress } from "../api";

export default function ParentDashboard() {
  const [profile, setProfile] = useState<ParentProfile | null>(null);
  const [progress, setProgress] = useState<ParentProgress | null>(null);
  const [consent, setConsent] = useState<{ statement: string; given: boolean; given_at: string | null } | null>(null);
  const [deletionRequested, setDeletionRequested] = useState(false);
  const [showDeletionConfirm, setShowDeletionConfirm] = useState(false);

  useEffect(() => {
    parentApi.me().then(setProfile);
    parentApi.progress().then(setProgress);
    parentApi.getConsent().then(setConsent);
  }, []);

  async function handleGiveConsent() {
    const result = await parentApi.giveConsent();
    setConsent((prev) => (prev ? { ...prev, given: result.given, given_at: result.given_at } : prev));
  }

  async function handleRequestDeletion() {
    await parentApi.requestDeletion();
    setDeletionRequested(true);
    setShowDeletionConfirm(false);
  }

  if (!profile) {
    return (
      <ParentLayout>
        <p>Loading...</p>
      </ParentLayout>
    );
  }

  const payUrl = `${window.location.origin}/pay?phone=${encodeURIComponent(profile.student_phone)}`;

  return (
    <ParentLayout>
      {consent && !consent.given && (
        <div className="card" style={{ marginBottom: 20, background: "rgba(43, 62, 196, 0.06)" }}>
          <p style={{ marginBottom: 12 }}>{consent.statement}</p>
          <button type="button" onClick={handleGiveConsent}>
            I Confirm & Consent
          </button>
        </div>
      )}

      <h1 style={{ marginBottom: 4 }}>{profile.student_name}</h1>
      <p className="muted" style={{ marginBottom: 20 }}>
        {profile.class ? `Class ${profile.class} · ` : ""}Hi {profile.parent_name || "there"}, here's how they're doing
      </p>

      <p className="balance" style={{ marginBottom: 4 }}>
        ₹{profile.credit_balance.toFixed(2)}
      </p>
      <p className="muted" style={{ marginBottom: 20, fontSize: 13 }}>AI credit balance</p>

      {progress && (
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
            <p>Syllabus coverage: {progress.coverage.covered.length}/{progress.coverage.total} NCERT chapters</p>
          )}
        </>
      )}

      <button
        type="button"
        style={{ width: "100%", marginTop: 20 }}
        onClick={() => window.location.assign(payUrl)}
      >
        Top Up AI Credits
      </button>

      <div style={{ marginTop: 32, paddingTop: 20, borderTop: "1px solid var(--border)" }}>
        <p className="muted" style={{ fontSize: 12, marginBottom: 8 }}>Privacy & Data</p>
        {deletionRequested ? (
          <p className="muted" style={{ fontSize: 13 }}>
            Deletion request submitted — Qlass will review and confirm once processed.
          </p>
        ) : showDeletionConfirm ? (
          <div>
            <p style={{ fontSize: 13, marginBottom: 8 }}>
              This permanently erases your child's name, phone, and chat history from Qlass. Are you sure?
            </p>
            <button type="button" onClick={handleRequestDeletion} style={{ marginRight: 8 }}>
              Yes, Request Deletion
            </button>
            <button type="button" onClick={() => setShowDeletionConfirm(false)}>
              Cancel
            </button>
          </div>
        ) : (
          <button type="button" onClick={() => setShowDeletionConfirm(true)} style={{ fontSize: 12 }}>
            Request Data Deletion
          </button>
        )}
      </div>
    </ParentLayout>
  );
}
