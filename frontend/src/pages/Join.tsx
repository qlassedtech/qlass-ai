import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { absoluteUrl, publicApi } from "../api";
import ThemeToggle from "../components/ThemeToggle";

const FEATURES = [
  { icon: "💬", title: "Ask Anything, Anytime", text: "Explain any topic in plain language, right on WhatsApp — no app to download." },
  { icon: "📸", title: "Photo Homework Help", text: "Stuck on a question? Send a photo and get it explained step by step." },
  { icon: "🎙️", title: "Voice Notes In & Out", text: "Ask out loud and hear the answer back — great for on-the-go learning." },
  { icon: "📝", title: "Quizzes & Mock Tests", text: "Real scored quizzes and timed, board-exam-style mock tests on any topic." },
  { icon: "📺", title: "Video Explanations", text: "A well-matched YouTube video whenever a topic needs more than text." },
  { icon: "📊", title: "Real Progress Tracking", text: "See your accuracy, weak topics, and streak — not guesses, real numbers." },
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
            <div className="landing-school-logos">
              <div className="logo-preview">
                {schoolLogo ? (
                  <img src={absoluteUrl(schoolLogo) || undefined} alt={schoolName} />
                ) : (
                  <span className="logo-placeholder" aria-hidden="true">🏫</span>
                )}
              </div>
              <span className="landing-logos-x">×</span>
              <img src="/logo.jpeg" alt="Qlass Learning" className="login-logo" style={{ margin: 0 }} />
            </div>
          ) : (
            <img src="/logo.jpeg" alt="Qlass Learning" className="login-logo" style={{ margin: "0 auto 8px" }} />
          )}
          <h1>{schoolName ? `${schoolName} × Qlass AI Tutor` : "Your Personal AI Tutor, on WhatsApp"}</h1>
          <p>
            {schoolName
              ? `Free, conversational AI tutoring for ${schoolName} students — explanations, quizzes, and homework help, all on WhatsApp.`
              : "Free, conversational tutoring for CBSE, ICSE, and State board students — explanations, quizzes, and homework help, all in the app you already use every day."}
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
              <p className="muted">Get ₹50 in free AI credits — no card, no download.</p>
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
      </div>
    </div>
  );
}
