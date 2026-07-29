import { useEffect, useState } from "react";
import ParentLayout from "../components/ParentLayout";
import { parentApi, type ParentProfile, type ParentProgress } from "../api";

export default function ParentDashboard() {
  const [profile, setProfile] = useState<ParentProfile | null>(null);
  const [progress, setProgress] = useState<ParentProgress | null>(null);

  useEffect(() => {
    parentApi.me().then(setProfile);
    parentApi.progress().then(setProgress);
  }, []);

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
    </ParentLayout>
  );
}
