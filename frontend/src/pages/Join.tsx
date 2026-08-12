import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { absoluteUrl, publicApi } from "../api";
import GoogleSignInButton from "../components/GoogleSignInButton";
import ThemeToggle from "../components/ThemeToggle";

/**
 * Pulls just the display name out of a Google ID token, client-side, with
 * no backend call — this is autofill convenience only, never proof of
 * identity. The actual identity check for this form stays the WhatsApp OTP
 * step right after (see publicApi.register/registerVerify): a student
 * still can't get trial credit without proving they control the phone
 * number, regardless of what a Google token claims.
 */
function decodeGoogleName(idToken: string): string | null {
  try {
    const payload = JSON.parse(atob(idToken.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
    return typeof payload.name === "string" ? payload.name : null;
  } catch {
    return null;
  }
}

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
  { icon: "📚", title: "Grounded in Your NCERT Textbook", text: "Answers are drawn directly from your NCERT syllabus, with the exact chapter cited — never a generic guess." },
];

// A school evaluating this page cares about a different question than a
// student does — not "will this help me learn" but "can I run my school on
// this." Every item here maps to a real, shipped feature (the admin
// console's own sidebar: Student Roster, Bulk Enrollment, Assign Quiz,
// Analytics, Billing & Credits, School Profile's approval toggle) rather
// than aspirational copy — a school that signs up on the strength of a
// claim this product doesn't back up is a support ticket waiting to happen.
const SCHOOL_BENEFITS = [
  { icon: "🏫", title: "Your School's Own Branded Portal", text: "Students see your school's name and logo, not a generic app — the sign-in and sign-up pages carry your identity." },
  { icon: "👩‍🏫", title: "One Dashboard for Every Teacher", text: "Enroll students, assign quizzes, generate worksheets and presentations, and see class-wide analytics from a single console." },
  { icon: "📲", title: "Nothing for Students to Install", text: "Every student already has WhatsApp — there's no app to download and no login to forget, which is exactly why adoption doesn't stall." },
  { icon: "✅", title: "You Decide Who Joins", text: "Auto-approve every signup, or review each one yourself before they're added to your roster — your school's call, changeable anytime." },
  { icon: "💳", title: "Transparent Usage, Not a Black Box", text: "See exactly how much AI usage each student and teacher account has drawn, in real time, from your billing dashboard." },
  { icon: "🔒", title: "Curriculum-Safe by Design", text: "Every answer is grounded in the NCERT syllabus and moderated for a school setting — not an open-ended chatbot with no guardrails." },
];

function SchoolBenefits({ benefits }: { benefits: typeof SCHOOL_BENEFITS }) {
  return (
    <div className="landing-school-band">
      <div className="landing-school-band-inner">
        <span className="landing-eyebrow landing-eyebrow-on-dark">For Schools</span>
        <h2>Bring Qlass to Your Whole School</h2>
        <p>
          The same AI tutor your students already love, wrapped in the tools your teachers and
          administrators actually need to run it.
        </p>
        <div className="landing-school-grid">
          {benefits.map((b) => (
            <div className="landing-school-tile" key={b.title}>
              <span className="landing-school-tile-icon" aria-hidden="true">{b.icon}</span>
              <div>
                <h3>{b.title}</h3>
                <p>{b.text}</p>
              </div>
            </div>
          ))}
        </div>
        <Link to="/register" className="button-link landing-school-cta">Register Your School</Link>
      </div>
    </div>
  );
}

type ChatMsg =
  | { from: string; kind: "text"; text: string; time: string }
  | { from: string; kind: "image"; caption: string; time: string }
  | { from: string; kind: "voice"; duration: string; time: string }
  | { from: string; kind: "pdf"; filename: string; meta: string; time: string }
  | { from: string; kind: "video"; title: string; duration: string; time: string; thumb: string };

// A rotating set of realistic exchanges, each demonstrating one capability
// from FEATURES — the fastest way to signal "this is a genuine AI tutor"
// is to show the conversation itself (see Character.AI's own landing page:
// a headline and a sign-up gate, nothing abstract). Animating through
// several scenarios turns the single static screenshot into something that
// actually demonstrates the product's breadth, GIF-style, without leaving
// the hero. Timestamps are real WhatsApp-style clock times — the first
// scenario deliberately uses a near-midnight time so "24/7" is something
// you can see, not just read.
const CHAT_SCENARIOS: { icon: string; label: string; messages: ChatMsg[] }[] = [
  {
    icon: "🕐",
    label: "Round-the-Clock Help",
    messages: [
      { from: "user", kind: "text", time: "11:47 PM", text: "I don't understand Newton's third law." },
      { from: "ai", kind: "text", time: "11:47 PM", text: "Good question! For every action, there's an equal and opposite reaction — push on a wall, and it pushes back on you with equal force." },
      { from: "user", kind: "text", time: "11:52 PM", text: "Is that how a rocket works?" },
      { from: "ai", kind: "text", time: "11:52 PM", text: "Exactly right. The rocket pushes exhaust gas downward, and the gas pushes the rocket upward. Does that make sense now?" },
    ],
  },
  {
    icon: "📸",
    label: "Photo Doubt Solving",
    messages: [
      { from: "user", kind: "image", time: "4:12 PM", caption: "A ball is thrown upward at 20 m/s. Find the time to reach maximum height. (g = 10 m/s²)" },
      { from: "ai", kind: "text", time: "4:13 PM", text: "Nice one — let's solve it together.\nAt maximum height, final velocity v = 0.\nUsing v = u − gt: 0 = 20 − 10t\nSo t = 2 seconds." },
    ],
  },
  {
    icon: "🎙️",
    label: "Ask By Voice",
    messages: [
      { from: "user", kind: "voice", time: "6:05 PM", duration: "0:14" },
      { from: "ai", kind: "voice", time: "6:05 PM", duration: "0:18" },
      { from: "ai", kind: "text", time: "6:05 PM", text: "Sending that as text too: speed is distance over time, velocity also includes direction — that's the key difference." },
    ],
  },
  {
    icon: "📄",
    label: "Full Homework Upload",
    messages: [
      { from: "user", kind: "pdf", time: "8:05 PM", filename: "Homework.pdf", meta: "8 questions" },
      { from: "ai", kind: "text", time: "8:06 PM", text: "Got your worksheet — I can see 8 questions. Let's work through them one at a time, starting with Question 1..." },
    ],
  },
  {
    icon: "📝",
    label: "Scored Quizzes",
    messages: [
      { from: "user", kind: "text", time: "6:30 PM", text: "Quiz me on photosynthesis." },
      { from: "ai", kind: "text", time: "6:30 PM", text: "Sure — let's see what you know!\nQ1: Which gas do plants release during photosynthesis?\nA) Carbon dioxide  B) Oxygen  C) Nitrogen" },
    ],
  },
  {
    icon: "🎬",
    label: "Video Explanations",
    messages: [
      { from: "user", kind: "text", time: "7:15 PM", text: "Can you show me a video on this?" },
      { from: "ai", kind: "video", time: "7:16 PM", title: "Diffusion Explained Simply", duration: "3:12", thumb: "🧪" },
    ],
  },
  {
    icon: "🌐",
    label: "Any Indian Language",
    messages: [
      { from: "user", kind: "text", time: "5:40 PM", text: "Hindi mein samjhao." },
      { from: "ai", kind: "text", time: "5:40 PM", text: "ज़रूर! प्रकाश संश्लेषण का मतलब है पौधों द्वारा भोजन बनाना।" },
    ],
  },
  {
    icon: "📚",
    label: "NCERT-Grounded Answers",
    messages: [
      { from: "user", kind: "text", time: "5:42 PM", text: "Where is this from?" },
      { from: "ai", kind: "text", time: "5:42 PM", text: "Straight from your NCERT textbook — Class 10 Science, Chapter 6: Life Processes, page 122." },
    ],
  },
  {
    icon: "📊",
    label: "Progress Tracking",
    messages: [
      { from: "user", kind: "text", time: "9:00 AM", text: "my progress" },
      { from: "ai", kind: "text", time: "9:00 AM", text: "📈 82% accuracy this week — up from 74% last week. Keep up the great work!" },
    ],
  },
];

// A short WhatsApp-style voice-note waveform — a fixed bar-height pattern
// rather than random per render, so it looks the same every time a voice
// message appears instead of jittering.
const VOICE_WAVE_HEIGHTS = [6, 12, 18, 10, 20, 14, 8, 16, 22, 12, 9, 17, 11, 6];

function ChatBubbleContent({ msg }: { msg: ChatMsg }) {
  if (msg.kind === "image") {
    return (
      <div className="landing-chat-media">
        <div className="landing-chat-media-thumb" aria-hidden="true">
          <div className="landing-chat-media-page">
            <span className="landing-chat-media-line" style={{ width: "70%" }} />
            <span className="landing-chat-media-line" style={{ width: "92%" }} />
            <span className="landing-chat-media-line" style={{ width: "55%" }} />
            <span className="landing-chat-media-formula">v = u − gt</span>
            <span className="landing-chat-media-line" style={{ width: "80%" }} />
          </div>
          <span className="landing-chat-media-badge">📷</span>
        </div>
        <span className="landing-chat-media-caption">{msg.caption}</span>
      </div>
    );
  }
  if (msg.kind === "voice") {
    return (
      <div className="landing-chat-voice">
        <span className="landing-chat-voice-play" aria-hidden="true">
          ▶
        </span>
        <span className="landing-chat-voice-wave" aria-hidden="true">
          {VOICE_WAVE_HEIGHTS.map((h, i) => (
            <span key={i} style={{ height: `${h}px` }} />
          ))}
        </span>
        <span className="landing-chat-voice-duration">{msg.duration}</span>
      </div>
    );
  }
  if (msg.kind === "pdf") {
    return (
      <div className="landing-chat-file">
        <span className="landing-chat-file-icon" aria-hidden="true">
          📄
        </span>
        <div className="landing-chat-file-info">
          <span className="landing-chat-file-name">{msg.filename}</span>
          <span className="landing-chat-file-meta">{msg.meta}</span>
        </div>
      </div>
    );
  }
  if (msg.kind === "video") {
    return (
      <div className="landing-chat-video">
        <div className="landing-chat-video-thumb" aria-hidden="true">
          <span className="landing-chat-video-thumb-icon">{msg.thumb}</span>
          <span className="landing-chat-video-play">▶</span>
          <span className="landing-chat-video-duration">{msg.duration}</span>
        </div>
        <span className="landing-chat-video-title">{msg.title}</span>
      </div>
    );
  }
  return <>{msg.text}</>;
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

// Animates through CHAT_SCENARIOS one message at a time, with a typing
// indicator before each AI reply — turns the static WhatsApp screenshot
// into a looping, GIF-like demo of the product's range of features.
function AnimatedChatDemo({ scenarios }: { scenarios: typeof CHAT_SCENARIOS }) {
  const [scenarioIndex, setScenarioIndex] = useState(0);
  const [visible, setVisible] = useState<ChatMsg[]>([]);
  const [typing, setTyping] = useState(false);
  const [fading, setFading] = useState(false);
  const bodyRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bodyRef.current?.scrollTo({ top: bodyRef.current.scrollHeight, behavior: "smooth" });
  }, [visible, typing]);

  useEffect(() => {
    let cancelled = false;
    const scenario = scenarios[scenarioIndex];
    setVisible([]);
    setFading(false);
    setTyping(false);

    async function run() {
      for (const msg of scenario.messages) {
        if (cancelled) return;
        if (msg.from === "ai") {
          setTyping(true);
          await sleep(900);
          if (cancelled) return;
          setTyping(false);
        } else {
          await sleep(500);
          if (cancelled) return;
        }
        setVisible((v) => [...v, msg]);
      }
      if (cancelled) return;
      await sleep(2600);
      if (cancelled) return;
      setFading(true);
      await sleep(350);
      if (cancelled) return;
      setScenarioIndex((i) => (i + 1) % scenarios.length);
    }
    run();
    return () => {
      cancelled = true;
    };
  }, [scenarioIndex, scenarios]);

  const scenario = scenarios[scenarioIndex];

  return (
    <div className="landing-chat-frame">
      <div className="landing-chat-preview">
        <div className="landing-chat-header">
          <span className="landing-chat-avatar" aria-hidden="true">
            <img src="/q-icon.png" alt="" />
          </span>
          Qlass AI Tutor
          <span className="landing-chat-demo-label">
            {scenario.icon} {scenario.label}
          </span>
        </div>
        <div ref={bodyRef} className={`landing-chat-body${fading ? " landing-chat-body-fading" : ""}`}>
          {visible.map((m, i) => (
            <div
              key={i}
              className={`landing-chat-bubble landing-chat-bubble-${m.from}${m.kind !== "text" ? " landing-chat-bubble-media" : ""}`}
            >
              <ChatBubbleContent msg={m} />
              <span className="landing-chat-time">{m.time}</span>
            </div>
          ))}
          {typing && (
            <div className="landing-chat-bubble landing-chat-bubble-ai landing-chat-typing" aria-label="Typing">
              <span />
              <span />
              <span />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

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
  const [studentClass, setStudentClass] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [registered, setRegistered] = useState<{ alreadyRegistered: boolean } | null>(null);
  // Set once the initial form is submitted and a WhatsApp OTP has been
  // sent — the form then switches to asking for that code instead of
  // creating the account immediately (see api.ts's publicApi.register vs
  // registerVerify). Trial credit is only granted after the OTP step, so
  // an unverified phone number can no longer farm free credit.
  const [otpRequired, setOtpRequired] = useState(false);
  const [otp, setOtp] = useState("");

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
      const result = await publicApi.register({ name, phone, school: schoolSlug, student_class: studentClass || undefined });
      if (!result.success) {
        setError(result.error || "Something went wrong — please try again");
        return;
      }
      if (result.otp_required) {
        setOtpRequired(true);
        return;
      }
      // Only reached for the "already registered" short-circuit — a new
      // signup always goes through otp_required above now.
      setRegistered({ alreadyRegistered: !!result.already_registered });
    } catch {
      setError("Something went wrong — please check your connection and try again");
    } finally {
      setLoading(false);
    }
  }

  async function handleVerifyOtp(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const result = await publicApi.registerVerify({
        name, phone, school: schoolSlug, student_class: studentClass || undefined, otp,
      });
      if (!result.success) {
        setError(result.error || "Incorrect code — please try again");
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
      ) : otpRequired ? (
        <form onSubmit={handleVerifyOtp}>
          <h2>Verify Your WhatsApp Number</h2>
          <p className="muted">We sent a code to {phone} on WhatsApp — enter it below to claim your free credits.</p>
          <label>
            Verification code
            <input
              value={otp}
              onChange={(e) => setOtp(e.target.value)}
              placeholder="6-digit code"
              inputMode="numeric"
              maxLength={6}
              required
            />
          </label>
          {error && <p className="error">{error}</p>}
          <button type="submit" disabled={loading}>
            {loading ? "Please wait..." : "Verify & Start Learning"}
          </button>
          <p className="auth-links">
            <a
              href="#"
              onClick={(e) => {
                e.preventDefault();
                setOtpRequired(false);
                setError(null);
              }}
            >
              Wrong number? Go back
            </a>
          </p>
        </form>
      ) : (
        <form onSubmit={handleSubmit}>
          <h2>Begin Learning for Free</h2>
          <p className="muted">Start with ₹50 in complimentary AI credits — no card required, no download needed.</p>
          <ul className="landing-form-checklist">
            <li>Free credits to get started</li>
            <li>No card or download required</li>
            <li>Replies arrive on WhatsApp instantly</li>
          </ul>
          {/* Autofill only — decoded client-side, never sent anywhere. The
              WhatsApp number below is still what actually proves who you
              are (see the OTP step after submitting), same as if you'd
              just typed your name in by hand. */}
          <GoogleSignInButton
            text="continue_with"
            onCredential={(idToken) => {
              const googleName = decodeGoogleName(idToken);
              if (googleName) setName(googleName);
            }}
          />
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
          <label>
            Class
            <select value={studentClass} onChange={(e) => setStudentClass(e.target.value)} required>
              <option value="" disabled>
                Select your class
              </option>
              {["3", "4", "5", "6", "7", "8", "9", "10", "11", "12"].map((c) => (
                <option key={c} value={c}>
                  Class {c}
                </option>
              ))}
              <option value="Other">Other</option>
            </select>
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
              <img src="/logo-tight.png" alt="Qlass Learning" />
            </div>
          </div>
        ) : (
          <img src="/logo-tight.png" alt="Qlass Learning" className="login-logo landing-logo-large" style={{ margin: "0 0 16px" }} />
        )}

        <div className="landing-hero-split">
          <div className="landing-hero-left">
            <div className="landing-hero-headline">
              <span className="landing-eyebrow">AI-Powered Academic Support</span>
              <h1>{schoolName ? `${schoolName}'s AI Academic Tutor` : "Your Personal AI Academic Tutor, on WhatsApp"}</h1>
              <p>
                {schoolName
                  ? `24/7 academic support from a dedicated AI tutor, directly on WhatsApp.`
                  : "Round-the-clock academic support, directly on WhatsApp — no app, no waiting."}
              </p>
              <ul className="landing-trust-pills">
                <li>🔒 Safe &amp; Moderated</li>
                <li>🎓 NCERT Aligned</li>
                <li>⏱️ 24/7 Availability</li>
              </ul>
            </div>
            <div className="landing-hero-demo">
              <AnimatedChatDemo scenarios={CHAT_SCENARIOS} />
            </div>
          </div>
          {/* On mobile this sits right after the headline, before the
              demo — a signup form buried below a full chat mockup meant
              scrolling past the whole demo before any action was
              possible. Desktop keeps it beside the hero as its own column
              (see .landing-hero-left / display:contents in index.css). */}
          <div className="landing-hero-form" id="signup">{registrationForm}</div>
        </div>

        <FeatureGrid features={FEATURES} />
      </div>

      <SchoolBenefits benefits={SCHOOL_BENEFITS} />

      <div className="landing-inner">
        <div className="landing-closing-cta">
          <h2>Ready to get started?</h2>
          <p className="muted">Registration takes under a minute — your first reply arrives on WhatsApp right away.</p>
          <a href="#signup" className="button-link">Begin Learning for Free</a>
        </div>

        <p className="landing-support-line">
          <span>📞 Enquiry/Technical Support —</span>{" "}
          <a href="tel:+919031003985">+91 9031003985</a>
          <span> / </span>
          <a href="tel:+919031003982">+91 9031003982</a>
          <span> | ✉️ </span>
          <a href="mailto:mailus@qlass.in">mailus@qlass.in</a>
        </p>
      </div>
    </div>
  );
}
