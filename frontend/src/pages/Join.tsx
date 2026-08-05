import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { absoluteUrl, publicApi } from "../api";
import ThemeToggle from "../components/ThemeToggle";

// Credible, academic register — this page is often embedded on a school's
// own website, so the tone needs to read as a serious learning product, not
// a casual consumer app. Every capability real competitors lead with (see
// audit: YoLearn.ai — 22+ languages + voice; YoTutor.AI — voice + photo
// doubt solver; MeraTutor.AI — 24/7, step-by-step, board-aligned) still gets
// its own card, named plainly rather than implied.
const FEATURES = [
  { icon: "🕐", title: "Round-the-Clock Academic Support", text: "Get clear, step-by-step help at any hour — not limited to school or tuition timings." },
  { icon: "📸", title: "Learn From a Photograph", text: "Share a photo of any question and receive a complete, step-by-step solution within moments." },
  { icon: "🎙️", title: "Ask Using Your Voice", text: "Speak a question instead of typing it, and receive a spoken explanation in return." },
  { icon: "🌐", title: "Available in Five Languages", text: "Learn in English, Hindi, Bhojpuri, Magahi, or Maithili — whichever you're most comfortable with." },
  { icon: "📄", title: "Submit a Full Assignment", text: "Upload an entire worksheet or homework file as a PDF or Word document, not one question at a time." },
  { icon: "📝", title: "Practice With Real Assessments", text: "Attempt scored quizzes and board-exam-style mock tests to identify exactly where you need to improve." },
  { icon: "🎬", title: "Video Explanations on Demand", text: "Receive a video explanation matched precisely to your question, not a generic search result." },
  { icon: "📊", title: "Track Academic Progress", text: "Monitor accuracy and improvement over time, backed by a clear record of performance." },
  { icon: "📚", title: "Grounded in Your Textbook", text: "Answers are drawn directly from your syllabus, with the exact chapter cited — never a generic guess." },
];

// A realistic exchange — the fastest way to signal "this is a genuine AI
// tutor" is to show the conversation itself, not describe it (see Character.
// AI's own landing page: a headline and a sign-up gate, nothing abstract).
// Kept natural but without slang, since this is meant to read as credible
// academic help, not a casual chat app.
const CHAT_PREVIEW = [
  { from: "user", text: "I don't understand Newton's third law." },
  { from: "ai", text: "For every action, there is an equal and opposite reaction. When you push against a wall, it pushes back on you with equal force." },
  { from: "user", text: "Is that how a rocket works?" },
  { from: "ai", text: "Exactly. The rocket pushes exhaust gas downward, and the gas pushes the rocket upward. Would you like to try a practice question on this?" },
];

// All nine features shown at once, not one at a time — an auto-rotating
// single card hid eight of nine features from anyone who didn't sit and
// wait, which read as "the page has no features." A bento-style grid keeps
// the premium icon-badge treatment but never hides content.
function FeatureGrid({ features }: { features: typeof FEATURES }) {
  return (
    <div className="landing-feature-grid">
      <p className="landing-spotlight-eyebrow">What You Get</p>
      <div className="landing-feature-grid-inner">
        {features.map((f) => (
          <div className="landing-feature-tile" key={f.title}>
            <span className="landing-spotlight-icon" aria-hidden="true">{f.icon}</span>
            <h3>{f.title}</h3>
            <p>{f.text}</p>
          </div>
        ))}
      </div>
    </div>
  );
}

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
          <h2>{registered.alreadyRegistered ? "Your Account Is Already Active" : "Registration Complete"}</h2>
          <p className="muted">
            {registered.alreadyRegistered
              ? "Check your WhatsApp — we've sent you a message to pick up where you left off."
              : "Check your WhatsApp for a welcome message with your free AI credits, and reply to begin learning."}
          </p>
        </div>
      ) : (
        <form onSubmit={handleSubmit}>
          <h2>Begin Learning for Free</h2>
          <p className="muted">Start with ₹50 in complimentary AI credits — no card required, no download needed.</p>
          <ul className="landing-form-checklist">
            <li>Free credits to get started</li>
            <li>No card or download required</li>
            <li>Replies arrive on WhatsApp instantly</li>
          </ul>
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
            {loading ? "Please wait..." : "Begin Learning for Free"}
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
        {schoolName ? (
          <div className="landing-brandbar">
            <div className="landing-brandbar-school">
              <div className="landing-school-logo-large">
                {schoolLogo ? (
                  <img src={absoluteUrl(schoolLogo) || undefined} alt={schoolName} />
                ) : (
                  <span className="logo-placeholder" aria-hidden="true">🏫</span>
                )}
              </div>
              <span className="landing-brandbar-name">{schoolName}</span>
            </div>
            <div className="landing-powered-by-chip">
              <span>Powered by</span>
              <img src="/logo.jpeg" alt="Qlass Learning" />
            </div>
          </div>
        ) : (
          <img src="/logo.jpeg" alt="Qlass Learning" className="login-logo" style={{ margin: "0 0 16px" }} />
        )}

        <div className="landing-hero-split">
          <div className="landing-hero-text">
            <span className="landing-eyebrow">AI-Powered Academic Support</span>
            <h1>{schoolName ? `${schoolName}'s AI Academic Tutor` : "Your Personal AI Academic Tutor, on WhatsApp"}</h1>
            <p>
              {schoolName
                ? `${schoolName} students receive round-the-clock academic support from a dedicated AI tutor, directly on WhatsApp.`
                : "Round-the-clock, step-by-step academic support — directly on WhatsApp. No app to download, no waiting for a reply."}
            </p>
            <ul className="landing-trust-pills">
              <li>🔒 Safe &amp; Moderated</li>
              <li>🎓 Curriculum Aligned</li>
              <li>⏱️ 24/7 Availability</li>
            </ul>
            <div className="landing-chat-frame">
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
          </div>
          <div className="landing-hero-form" id="signup">{registrationForm}</div>
        </div>

        <FeatureGrid features={FEATURES} />

        <div className="landing-closing-cta">
          <h2>Ready to get started?</h2>
          <p className="muted">Registration takes under a minute — your first reply arrives on WhatsApp right away.</p>
          <a href="#signup" className="button-link">Begin Learning for Free</a>
        </div>
      </div>
    </div>
  );
}
