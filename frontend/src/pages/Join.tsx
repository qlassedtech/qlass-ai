import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { absoluteUrl, publicApi } from "../api";
import ThemeToggle from "../components/ThemeToggle";

// Written for the student actually reading this, not a parent or a
// procurement buyer — casual, specific, relatable moments (11pm before an
// exam, re-asking the same doubt in class) instead of generic SaaS-speak.
// Every capability real competitors lead with (see audit: YoLearn.ai —
// 22+ languages + voice; YoTutor.AI — voice + photo doubt solver;
// MeraTutor.AI — 24/7, step-by-step, board-aligned) gets its own card here
// too, explicitly named — nothing left implied.
const FEATURES = [
  { icon: "😩➡️😌", title: "Stuck at 11pm? Not anymore.", text: "24/7, not just class hours — ask the second you're stuck, get a real answer back." },
  { icon: "📸", title: "Snap it, solve it", text: "Photo of a tricky problem in, a step-by-step explanation out. No typing the whole thing." },
  { icon: "🎙️", title: "Too lazy to type? Just talk.", text: "Say it out loud, hear it back — like explaining it to a friend who actually knows the answer." },
  { icon: "🗣️", title: "In your own language", text: "English, Hindi, Bhojpuri, Magahi, or Maithili — say it however you actually talk." },
  { icon: "📄", title: "Whole worksheet? No problem", text: "Share a PDF or Word file of your homework, not just one question at a time." },
  { icon: "🎯", title: "Actually test yourself", text: "Real scored quizzes and board-exam mock tests — find out what you don't know before the exam does." },
  { icon: "📺", title: "When text isn't enough", text: "A video that matches exactly what you asked, not a random search result." },
  { icon: "📈", title: "Watch your streak grow", text: "Real accuracy, real progress — proof you're actually getting better, not just busy." },
  { icon: "📚", title: "Straight from your textbook", text: "Not a guess — when we teach from your syllabus, we cite the actual chapter it came from." },
];

// A fake but realistic exchange — the fastest way to signal "this is an AI
// chat product" is to just show the chat, not describe it (see Character.
// AI's own landing page: a headline and a sign-up gate, nothing abstract).
const CHAT_PREVIEW = [
  { from: "user", text: "I don't get Newton's third law 😩" },
  { from: "ai", text: "Easy — for every action, there's an equal and opposite reaction. Push a wall, it pushes back just as hard 💪" },
  { from: "user", text: "ohh so like a rocket?" },
  { from: "ai", text: "Exactly! Rocket pushes gas down, gas pushes rocket up 🚀 Want to try a quick question on it?" },
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

  const registrationForm = (
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
            Already learning with us? <Link to={schoolSlug ? `/login?school=${schoolSlug}` : "/login"}>Log in</Link>
          </p>
        </form>
      )}
    </div>
  );

  return (
    <div className="landing-page">
      <div style={{ position: "absolute", top: 20, right: 20, zIndex: 2 }}>
        <ThemeToggle />
      </div>
      <div className="landing-inner">
        <div className="landing-hero-split">
          <div className="landing-hero-text">
            {schoolName ? (
              <div className="landing-school-logo-large">
                {schoolLogo ? (
                  <img src={absoluteUrl(schoolLogo) || undefined} alt={schoolName} />
                ) : (
                  <span className="logo-placeholder" aria-hidden="true">🏫</span>
                )}
              </div>
            ) : (
              <img src="/logo.jpeg" alt="Qlass Learning" className="login-logo" style={{ margin: "0 0 8px" }} />
            )}
            <h1>{schoolName ? `${schoolName}'s Own AI Tutor` : "Your Own AI Tutor. On WhatsApp. Free."}</h1>
            <p>
              {schoolName
                ? `Stuck on homework at 11pm? ${schoolName} students get a real AI tutor, 24/7, right on WhatsApp.`
                : "Stuck on homework at 11pm? Get a real AI tutor, 24/7, right on WhatsApp — no app, no waiting."}
            </p>
            <div className="landing-chat-preview">
              <div className="landing-chat-header">
                <span className="landing-chat-avatar" aria-hidden="true">
                  <img src="/q-icon.png" alt="" />
                </span>
                Qlass AI Tutor
              </div>
              <div className="landing-chat-body">
                {CHAT_PREVIEW.map((m, i) => (
                  <div key={i} className={`landing-chat-bubble landing-chat-bubble-${m.from}`}>
                    {m.text}
                  </div>
                ))}
              </div>
            </div>
          </div>
          <div className="landing-hero-form">{registrationForm}</div>
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
