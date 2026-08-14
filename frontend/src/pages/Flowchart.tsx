import { useState } from "react";
import { Link } from "react-router-dom";
import ThemeToggle from "../components/ThemeToggle";
import FlowDiagram, { type FlowEdge, type FlowNode } from "../components/FlowDiagram";

interface Diagram {
  height: number;
  nodes: FlowNode[];
  edges: FlowEdge[];
  title: string;
  desc: string;
}

interface Segment {
  id: string;
  group: string;
  label: string;
  intro: string;
  diagrams: Diagram[];
}

// Every diagram below reflects code actually read in this repo (routers,
// services, and live-tested behavior), not an assumed design — see the
// individual descriptions for what each node/edge corresponds to.
const SEGMENTS: Segment[] = [
  {
    id: "entry",
    group: "Getting in",
    label: "Full entry-point graph",
    intro:
      "Every way into the product and how the pages connect. Home links both ways to Login and to Register; " +
      "Register links one way back to Login. Google Sign-In and Sign-Up are alternate paths into Login and " +
      "Register respectively. WhatsApp is fed by Home (OTP delivery) but nothing links back out of it — a " +
      "person can also reach it organically with zero prior web contact.",
    diagrams: [
      {
        height: 420,
        title: "Full entry-point navigation graph",
        desc: "Home, Login, Register, Forgot password, Google Sign-In, Google Sign-Up, and WhatsApp, with every real link between them.",
        nodes: [
          { id: "home", x: 250, y: 50, w: 180, h: 56, title: "Home", subtitle: "/ and /join", tone: "neutral" },
          { id: "whatsapp", x: 440, y: 50, w: 170, h: 56, title: "WhatsApp", subtitle: "OTP + organic chat", tone: "neutral" },
          { id: "login", x: 70, y: 190, w: 170, h: 56, title: "Login", subtitle: "/login", tone: "accent" },
          { id: "register", x: 440, y: 190, w: 170, h: 56, title: "Register", subtitle: "/register", tone: "success" },
          { id: "forgot", x: 70, y: 330, w: 170, h: 56, title: "Forgot password", subtitle: "/forgot-password", tone: "neutral" },
          { id: "gsignin", x: 250, y: 260, w: 170, h: 56, title: "Google Sign-In", subtitle: "existing account only", tone: "accent" },
          { id: "gsignup", x: 450, y: 260, w: 170, h: 56, title: "Google Sign-Up", subtitle: "alt to password", tone: "success" },
        ],
        edges: [
          { points: [[280, 106], [155, 190]], bidirectional: true },
          { points: [[400, 106], [525, 190]], bidirectional: true },
          { points: [[440, 218], [240, 218]] },
          { points: [[155, 246], [155, 330]], bidirectional: true },
          { points: [[335, 260], [240, 225]] },
          { points: [[535, 260], [525, 246]] },
          { points: [[430, 78], [440, 78]] },
        ],
      },
    ],
  },
  {
    id: "staff-login",
    group: "Login routing",
    label: "Staff login",
    intro:
      "A teacher or admin phone number gets a password form on /login. After signing in, the account's role " +
      "decides the destination — org_admin lands on the schools console, everyone else lands on their own " +
      "school's students console.",
    diagrams: [
      {
        height: 420,
        title: "Staff login flow",
        desc: "Teacher or admin phone gets a password form; role after login determines students or schools console.",
        nodes: [
          { id: "1", x: 230, y: 40, w: 220, h: 56, title: "Enter phone on /login", subtitle: "teacher or admin number", tone: "neutral" },
          { id: "2", x: 230, y: 140, w: 220, h: 56, title: "Password form shown", subtitle: "enter password", tone: "accent" },
          { id: "3", x: 230, y: 240, w: 220, h: 56, title: "Signed in", subtitle: "role determines redirect", tone: "accent" },
          { id: "4a", x: 60, y: 340, w: 220, h: 56, title: "admin / teacher", subtitle: "lands on /students", tone: "success" },
          { id: "4b", x: 400, y: 340, w: 220, h: 56, title: "org_admin", subtitle: "lands on /schools", tone: "success" },
        ],
        edges: [
          { points: [[340, 96], [340, 140]] },
          { points: [[340, 196], [340, 240]] },
          { points: [[340, 296], [170, 340]] },
          { points: [[340, 296], [510, 340]] },
        ],
      },
    ],
  },
  {
    id: "parent-login",
    group: "Login routing",
    label: "Parent login",
    intro:
      "A phone number a school has linked as a parent contact gets WhatsApp OTP on /login. After verifying, " +
      "the parent lands on a separate read-only progress and billing view for their child — not the school console.",
    diagrams: [
      {
        height: 530,
        title: "Parent login flow",
        desc: "A linked parent contact gets WhatsApp OTP on /login, verifies it, and lands on a read-only parent view.",
        nodes: [
          { id: "1", x: 230, y: 40, w: 220, h: 56, title: "Enter phone on /login", subtitle: "linked parent contact", tone: "neutral" },
          { id: "2", x: 230, y: 140, w: 220, h: 56, title: "Recognized as parent", subtitle: "linked by the school", tone: "success" },
          { id: "3", x: 230, y: 240, w: 220, h: 56, title: "WhatsApp OTP sent", subtitle: "to that phone number", tone: "success" },
          { id: "4", x: 230, y: 340, w: 220, h: 56, title: "Enter code to verify", subtitle: "proves phone ownership", tone: "success" },
          { id: "5", x: 230, y: 440, w: 220, h: 56, title: "Signed in", subtitle: "read-only /parent view", tone: "neutral" },
        ],
        edges: [
          { points: [[340, 96], [340, 140]] },
          { points: [[340, 196], [340, 240]] },
          { points: [[340, 296], [340, 340]] },
          { points: [[340, 396], [340, 440]] },
        ],
      },
    ],
  },
  {
    id: "student-login",
    group: "Login routing",
    label: "Student login",
    intro:
      "A returning student's phone is checked for a portal password (rare — only for students with no WhatsApp " +
      "access). If set, they get a password form; otherwise WhatsApp OTP, the common case. Both paths land on " +
      "the same chat, unlike staff and parent which lead to genuinely different products.",
    diagrams: [
      {
        height: 420,
        title: "Student login flow",
        desc: "Portal password if set, otherwise WhatsApp OTP — both land on the same student chat.",
        nodes: [
          { id: "1", x: 230, y: 40, w: 220, h: 56, title: "Enter phone on /login", subtitle: "not staff, not a parent", tone: "neutral" },
          { id: "2a", x: 60, y: 140, w: 220, h: 56, title: "Portal password set?", subtitle: "rare — no WhatsApp access", tone: "neutral" },
          { id: "2b", x: 400, y: 140, w: 220, h: 56, title: "No password on file", subtitle: "the common case", tone: "neutral" },
          { id: "3a", x: 60, y: 240, w: 220, h: 56, title: "Password login", tone: "accent" },
          { id: "3b", x: 400, y: 240, w: 220, h: 56, title: "WhatsApp OTP", tone: "accent" },
          { id: "4", x: 230, y: 340, w: 220, h: 56, title: "Signed in", subtitle: "lands on /chat", tone: "success" },
        ],
        edges: [
          { points: [[340, 96], [170, 140]] },
          { points: [[340, 96], [510, 140]] },
          { points: [[170, 196], [170, 240]] },
          { points: [[510, 196], [510, 240]] },
          { points: [[170, 296], [340, 340]] },
          { points: [[510, 296], [340, 340]] },
        ],
      },
    ],
  },
  {
    id: "google-signin",
    group: "Google",
    label: "Google Sign-In",
    intro:
      "Clicking Google on /login first tries a teacher/admin match, then a student match. If neither matches, " +
      "an error is shown — Google sign-in never creates an account, so the person has to sign in by phone " +
      "first and link Google afterward.",
    diagrams: [
      {
        height: 430,
        title: "Google Sign-In fallback chain",
        desc: "Try teacher match, then student match, then show a no-account error — Google sign-in never creates an account.",
        nodes: [
          { id: "1", x: 170, y: 40, w: 250, h: 56, title: "Google button clicked", subtitle: "returns an ID token", tone: "neutral" },
          { id: "2", x: 170, y: 140, w: 250, h: 56, title: "Try teacher/admin login", subtitle: "matches Teacher.email?", tone: "accent" },
          { id: "2s", x: 440, y: 140, w: 190, h: 56, title: "Signed in", subtitle: "role-based redirect", tone: "success" },
          { id: "3", x: 170, y: 240, w: 250, h: 56, title: "Try student login", subtitle: "matches Student.email?", tone: "accent" },
          { id: "3s", x: 440, y: 240, w: 190, h: 56, title: "Signed in", subtitle: "lands on /chat", tone: "success" },
          { id: "4", x: 170, y: 340, w: 250, h: 56, title: "No account linked", subtitle: "sign in via phone first", tone: "error" },
        ],
        edges: [
          { points: [[295, 96], [295, 140]] },
          { points: [[420, 168], [440, 168]] },
          { points: [[295, 196], [295, 240]] },
          { points: [[420, 268], [440, 268]] },
          { points: [[295, 296], [295, 340]] },
        ],
      },
    ],
  },
  {
    id: "google-signup",
    group: "Google",
    label: "Google Sign-Up & linking",
    intro:
      "Three separate contexts, none of them checking for an existing account. On /register, Google supplies " +
      "identity as an alternative to a password when creating a NEW school. On /join, Google only autofills the " +
      "name field client-side — WhatsApp OTP is still what actually creates the account. In student settings, an " +
      "already-signed-in student can link Google as an alternate future login method.",
    diagrams: [
      {
        height: 350,
        title: "Google Sign-Up and linking — three contexts",
        desc: "Register uses Google as an alt to a password. Join only autofills a name. Student settings links Google to an existing account.",
        nodes: [
          { id: "1a", x: 30, y: 60, w: 180, h: 56, title: "Fill school details", subtitle: "name, city, board", tone: "neutral" },
          { id: "1b", x: 30, y: 160, w: 180, h: 56, title: "Google as identity", subtitle: "alt to password", tone: "success" },
          { id: "1c", x: 30, y: 260, w: 180, h: 56, title: "Account created", subtitle: "signed in immediately", tone: "success" },
          { id: "2a", x: 240, y: 60, w: 180, h: 56, title: "Click Google", subtitle: "button on /join", tone: "neutral" },
          { id: "2b", x: 240, y: 160, w: 180, h: 56, title: "Name autofilled", subtitle: "client-side only", tone: "accent" },
          { id: "2c", x: 240, y: 260, w: 180, h: 56, title: "OTP still required", subtitle: "phone proves identity", tone: "accent" },
          { id: "3a", x: 450, y: 60, w: 180, h: 56, title: "Signed in via phone", subtitle: "existing account", tone: "neutral" },
          { id: "3b", x: 450, y: 160, w: 180, h: 56, title: "Link Google", subtitle: "adds as alt login", tone: "accent" },
          { id: "3c", x: 450, y: 260, w: 180, h: 56, title: "Either now works", subtitle: "next login, either way", tone: "accent" },
        ],
        edges: [
          { points: [[120, 116], [120, 160]] },
          { points: [[120, 216], [120, 260]] },
          { points: [[330, 116], [330, 160]] },
          { points: [[330, 216], [330, 260]] },
          { points: [[540, 116], [540, 160]] },
          { points: [[540, 216], [540, 260]] },
        ],
      },
    ],
  },
  {
    id: "outcomes",
    group: "Login routing",
    label: "Where sign-in leads",
    intro:
      "A successful Login can land on four different destinations depending on account type. A successful " +
      "Register also lands on the students console, for the newly created school's own admin.",
    diagrams: [
      {
        height: 270,
        title: "Where a successful sign-in leads",
        desc: "Login can lead to Chat, a parent view, Schools console, or Students console depending on account type. Register also leads to Students.",
        nodes: [
          { id: "login", x: 170, y: 40, w: 170, h: 56, title: "Login success", subtitle: "by account type", tone: "accent" },
          { id: "register", x: 440, y: 40, w: 170, h: 56, title: "Register success", subtitle: "school created", tone: "success" },
          { id: "chat", x: 40, y: 180, w: 130, h: 56, title: "Chat", subtitle: "student", tone: "accent" },
          { id: "parent", x: 190, y: 180, w: 130, h: 56, title: "Parent view", subtitle: "read-only", tone: "success" },
          { id: "schools", x: 340, y: 180, w: 130, h: 56, title: "Schools", subtitle: "org_admin", tone: "accent" },
          { id: "students", x: 490, y: 180, w: 130, h: 56, title: "Students", subtitle: "admin/teacher", tone: "accent" },
        ],
        edges: [
          { points: [[255, 96], [105, 180]] },
          { points: [[255, 96], [255, 180]] },
          { points: [[255, 96], [405, 180]] },
          { points: [[255, 96], [555, 180]] },
          { points: [[525, 96], [555, 180]] },
        ],
      },
    ],
  },
  {
    id: "tutoring",
    group: "Core product",
    label: "Tutoring chat loop",
    intro:
      "A message arrives on WhatsApp or web chat. If credits are out, payment options are shown instead. If " +
      "the student's profile is missing a field, one question is asked per turn. Otherwise an answer is " +
      "generated by the model matching the student's tutor level, sent back, and credit is deducted.",
    diagrams: [
      {
        height: 530,
        title: "Core tutoring chat loop",
        desc: "Credit check, then profile check, then a level-matched answer with credit deducted.",
        nodes: [
          { id: "1", x: 170, y: 40, w: 250, h: 56, title: "Message received", subtitle: "WhatsApp or web chat", tone: "neutral" },
          { id: "2", x: 170, y: 140, w: 250, h: 56, title: "Credits available?", subtitle: "wallet balance check", tone: "neutral" },
          { id: "2b", x: 470, y: 140, w: 190, h: 56, title: "Out of credits", subtitle: "payment options shown", tone: "error" },
          { id: "3", x: 170, y: 240, w: 250, h: 56, title: "Profile complete?", subtitle: "name, class, board", tone: "neutral" },
          { id: "3b", x: 470, y: 240, w: 190, h: 56, title: "Ask one field", subtitle: "name, class, or board", tone: "accent" },
          { id: "4", x: 170, y: 340, w: 250, h: 56, title: "Generate answer", subtitle: "model by tutor level", tone: "accent" },
          { id: "5", x: 170, y: 440, w: 250, h: 56, title: "Reply sent", subtitle: "credit deducted", tone: "success" },
        ],
        edges: [
          { points: [[295, 96], [295, 140]] },
          { points: [[420, 168], [470, 168]] },
          { points: [[295, 196], [295, 240]] },
          { points: [[420, 268], [470, 268]] },
          { points: [[295, 296], [295, 340]] },
          { points: [[295, 396], [295, 440]] },
        ],
      },
    ],
  },
  {
    id: "billing",
    group: "Core product",
    label: "Credits & billing",
    intro:
      "A student starts with trial credit granted at signup. Credits are spent per feature used. At 50% and " +
      "75% of the balance, a low-balance nudge is sent on WhatsApp. When the balance reaches zero, further " +
      "replies are blocked until the student or their school pays to continue.",
    diagrams: [
      {
        height: 430,
        title: "Credit and billing lifecycle",
        desc: "Trial credit, per-feature spend, low-balance nudges at 50/75%, then blocked until payment.",
        nodes: [
          { id: "1", x: 230, y: 40, w: 220, h: 56, title: "Trial credit granted", subtitle: "at signup", tone: "neutral" },
          { id: "2", x: 230, y: 140, w: 220, h: 56, title: "Credits spent per use", subtitle: "per feature, metered", tone: "neutral" },
          { id: "2s", x: 480, y: 140, w: 170, h: 56, title: "Low-balance nudge", subtitle: "at 50% and 75%", tone: "success" },
          { id: "3", x: 230, y: 240, w: 220, h: 56, title: "Balance reaches zero", subtitle: "further replies blocked", tone: "error" },
          { id: "4", x: 230, y: 340, w: 220, h: 56, title: "Pay to continue", subtitle: "student or school pays", tone: "accent" },
        ],
        edges: [
          { points: [[340, 96], [340, 140]] },
          { points: [[450, 168], [480, 168]] },
          { points: [[340, 196], [340, 240]] },
          { points: [[340, 296], [340, 340]] },
        ],
      },
    ],
  },
  {
    id: "school-approval",
    group: "Core product",
    label: "School approval",
    intro:
      "A school self-registers via /register and Qlass staff get a WhatsApp alert with the school and admin " +
      "details. Approve sets the school active. Reject sets it churned — but only if the school has no real " +
      "students or teachers enrolled yet; otherwise the tap is refused and rejection must go through the portal.",
    diagrams: [
      {
        height: 330,
        title: "Self-registered school approval flow",
        desc: "WhatsApp alert to staff with Approve and Reject buttons; Reject is guarded against schools with real activity already.",
        nodes: [
          { id: "1", x: 230, y: 40, w: 220, h: 56, title: "School self-registers", subtitle: "via /register", tone: "neutral" },
          { id: "2", x: 230, y: 140, w: 220, h: 56, title: "WhatsApp alert to staff", subtitle: "school + admin details", tone: "neutral" },
          { id: "3a", x: 60, y: 240, w: 220, h: 56, title: "Approve", subtitle: "status becomes active", tone: "success" },
          { id: "3b", x: 400, y: 240, w: 220, h: 56, title: "Reject", subtitle: "only if no real users yet", tone: "error" },
        ],
        edges: [
          { points: [[340, 96], [340, 140]] },
          { points: [[340, 196], [170, 240]] },
          { points: [[340, 196], [510, 240]] },
        ],
      },
    ],
  },
  {
    id: "bulk-upload",
    group: "Admin operations",
    label: "Bulk student upload",
    intro: "An admin uploads a roster file, reviews a preview of valid and invalid rows, then confirms — creating every valid student with trial credit.",
    diagrams: [
      {
        height: 330,
        title: "Bulk student upload flow",
        desc: "Upload a roster file, preview valid and invalid rows, confirm, and students are created with trial credit.",
        nodes: [
          { id: "1", x: 230, y: 40, w: 220, h: 56, title: "Upload CSV/Excel", subtitle: "student roster file", tone: "neutral" },
          { id: "2", x: 230, y: 140, w: 220, h: 56, title: "Preview parsed rows", subtitle: "valid vs invalid shown", tone: "neutral" },
          { id: "3", x: 230, y: 240, w: 220, h: 56, title: "Confirm import", subtitle: "admin reviews first", tone: "accent" },
        ],
        edges: [
          { points: [[340, 96], [340, 140]] },
          { points: [[340, 196], [340, 240]] },
        ],
      },
    ],
  },
  {
    id: "quiz-assign",
    group: "Admin operations",
    label: "Quiz assignment",
    intro: "A teacher picks a chapter and a student or class; a quiz is generated from the ingested NCERT content and sent through chat, then scored automatically.",
    diagrams: [
      {
        height: 330,
        title: "Quiz assignment flow",
        desc: "Pick a chapter and students, generate a quiz, send it via chat, and it's attempted and scored automatically.",
        nodes: [
          { id: "1", x: 230, y: 40, w: 220, h: 56, title: "Pick chapter + students", subtitle: "or whole class", tone: "neutral" },
          { id: "2", x: 230, y: 140, w: 220, h: 56, title: "Quiz generated", subtitle: "from NCERT content", tone: "accent" },
          { id: "3", x: 230, y: 240, w: 220, h: 56, title: "Attempted + scored", subtitle: "results in analytics", tone: "success" },
        ],
        edges: [
          { points: [[340, 96], [340, 140]] },
          { points: [[340, 196], [340, 240]] },
        ],
      },
    ],
  },
  {
    id: "payment",
    group: "Admin operations",
    label: "Subscription payment",
    intro: "A Razorpay order is created, the payer completes checkout, the webhook verifies the payment signature, and credit or a subscription is activated immediately.",
    diagrams: [
      {
        height: 330,
        title: "Subscription payment flow",
        desc: "Create a Razorpay order, complete checkout, the webhook verifies it, and the plan is activated.",
        nodes: [
          { id: "1", x: 230, y: 40, w: 220, h: 56, title: "Create order", subtitle: "amount + plan", tone: "neutral" },
          { id: "2", x: 230, y: 140, w: 220, h: 56, title: "Razorpay checkout", subtitle: "student or school pays", tone: "accent" },
          { id: "3", x: 230, y: 240, w: 220, h: 56, title: "Webhook verifies", subtitle: "signature checked", tone: "accent" },
          { id: "4", x: 230, y: 340, w: 220, h: 56, title: "Plan activated", subtitle: "immediately usable", tone: "success" },
        ],
        edges: [
          { points: [[340, 96], [340, 140]] },
          { points: [[340, 196], [340, 240]] },
          { points: [[340, 296], [340, 340]] },
        ],
      },
    ],
  },
];

const GROUPS = Array.from(new Set(SEGMENTS.map((s) => s.group)));

export default function Flowchart() {
  const [activeId, setActiveId] = useState(SEGMENTS[0].id);
  const active = SEGMENTS.find((s) => s.id === activeId) || SEGMENTS[0];

  return (
    <div className="app-shell">
      <nav className="sidebar">
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 8 }}>
          <img src="/logo-tight.png" alt="Qlass Learning" className="sidebar-logo" />
          <ThemeToggle />
        </div>
        {GROUPS.map((group) => (
          <div className="nav-group" key={group}>
            <span className="nav-group-label">{group}</span>
            {SEGMENTS.filter((s) => s.group === group).map((s) => (
              <a
                key={s.id}
                href="#"
                className={s.id === activeId ? "active" : ""}
                onClick={(e) => {
                  e.preventDefault();
                  setActiveId(s.id);
                }}
              >
                {s.label}
              </a>
            ))}
          </div>
        ))}
        <Link to="/" className="logout" style={{ textAlign: "center", textDecoration: "none", display: "block" }}>
          Back to Qlass
        </Link>
      </nav>
      <main className="content">
        <div className="page-header">
          <h1>{active.label}</h1>
        </div>
        <p className="muted" style={{ marginBottom: 24, maxWidth: 760 }}>{active.intro}</p>
        {active.diagrams.map((diagram, i) => (
          <div className="card" key={i} style={{ marginBottom: 24, padding: 24 }}>
            <FlowDiagram height={diagram.height} nodes={diagram.nodes} edges={diagram.edges} title={diagram.title} desc={diagram.desc} />
          </div>
        ))}
      </main>
    </div>
  );
}
