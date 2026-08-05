import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { absoluteUrl, publicApi } from "../api";
import ThemeToggle from "../components/ThemeToggle";

// Short, benefit-first tagline + one concrete line — same pattern real AI-
// tutor competitors use (e.g. "Smarter study, stronger results"), not a
// plain feature description.
const FEATURES = [
  { icon: "💬", title: "Never Stuck Again", text: "Real answers in seconds, on any topic — right inside the chat you already have open." },
  { icon: "📸", title: "Snap It, Solve It", text: "Photo of a tricky problem in, a step-by-step explanation out." },
  { icon: "🎙️", title: "Just Talk It Out", text: "Ask out loud, hear it back — studying that fits around your day, not the other way." },
  { icon: "📝", title: "Prove What You Know", text: "Real scored quizzes and board-exam-style mock tests, the moment you're ready." },
  { icon: "📺", title: "See It, Not Just Read It", text: "A perfectly matched video the second text alone won't cut it." },
  { icon: "📊", title: "Watch Yourself Improve", text: "Real accuracy, real streaks, real proof — not a guess at how you're doing." },
];

export default function Join() {
  const [searchParams] = useSearchParams();
  const schoolSlug = searchParams.get("school") || undefined;
  const [schoolName, setSchoolName] = useState<string | null>(null);
  const [schoolLogo, setSchoolLogo] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [registered, setRegistered] = useState<{ alreadyRegistered: boolean } | null>(null);

  useEffect(() => {
    if (schoolSlug) {
      publicApi.schoolInfo(schoolSlug).then((res) => {
        setSchoolName(res.name);
        setSchoolLogo(res.logo_url);
      });
    }
  }, [schoolSlug]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const result = await publicApi.register({ name, phone, school: schoolSlug });
      if (!result.success) {
        setError(result.error || "Something went wrong — please try again");
        return;
      }
      setRegistered({ alreadyRegistered: !!result.already_registered });
    } catch {
      setError("Something went wrong — please check your connection and try again");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="landing-page">
      <div style={{ position: "absolute", top: 20, right: 20, zIndex: 2 }}>
        <ThemeToggle />
      </div>
      <div className="landing-inner">
        <div className="landing-hero">
          {schoolName ? (
            // School branding leads — large, dominant, the actual partner
            // the student recognizes. Qlass steps back to a small "Powered
            // by" credit near the form instead of co-branding equally at
            // the top (see .landing-powered-by below) — this is the
            // school's own page, not a joint one.
            <div className="landing-school-logo-large">
              {schoolLogo ? (
                <img src={absoluteUrl(schoolLogo) || undefined} alt={schoolName} />
              ) : (
                <span className="logo-placeholder" aria-hidden="true">🏫</span>
              )}
            </div>
          ) : (
            <img src="/logo.jpeg" alt="Qlass Learning" className="login-logo" style={{ margin: "0 auto 8px" }} />
          )}
          <h1>{schoolName ? `${schoolName}'s Own AI Tutor` : "Your Own AI Tutor. On WhatsApp. Free."}</h1>
          <p>
            {schoolName
              ? `24/7 AI tutoring built for ${schoolName} students — real explanations, real quizzes, zero app to download.`
              : "24/7 tutoring for CBSE, ICSE, and State board students — real explanations, real quizzes, zero app to download."}
          </p>
        </div>

        <div className="feature-grid">
          {FEATURES.map((f) => (
            <div className="feature-card" key={f.title}>
              <span className="feature-icon">{f.icon}</span>
              <h3>{f.title}</h3>
              <p>{f.text}</p>
            </div>
          ))}
        </div>

        <div className="card landing-form-card">
          {registered ? (
            <div className="landing-success">
              <span className="landing-success-icon">✅</span>
              <h2>{registered.alreadyRegistered ? "You're already set up!" : "You're in! 🎉"}</h2>
              <p className="muted">
                {registered.alreadyRegistered
                  ? "Check your WhatsApp — we've sent you a message to pick up where you left off."
                  : "Check your WhatsApp for a welcome message with your free AI credits — just reply to start learning."}
              </p>
            </div>
          ) : (
            <form onSubmit={handleSubmit}>
              <h2>Start Learning Free</h2>
              <p className="muted">₹50 in free AI credits — no card, no download, no waiting.</p>
              <label>
                Your name
                <input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Priya Sharma" required />
              </label>
              <label>
                WhatsApp number
                <input
                  value={phone}
                  onChange={(e) => setPhone(e.target.value)}
                  placeholder="10-digit mobile number"
                  inputMode="numeric"
                  required
                />
              </label>
              {error && <p className="error">{error}</p>}
              <button type="submit" disabled={loading}>
                {loading ? "Please wait..." : "Start Learning Free"}
              </button>
              <p className="auth-links">
                Already learning with us?{" "}
                <Link to={schoolSlug ? `/login?school=${schoolSlug}` : "/login"}>Log in</Link>
              </p>
            </form>
          )}
        </div>

        {schoolName && (
          <div className="landing-powered-by">
            <span>Powered by</span>
            <img src="/logo.jpeg" alt="Qlass Learning" />
          </div>
        )}
      </div>
    </div>
  );
}
