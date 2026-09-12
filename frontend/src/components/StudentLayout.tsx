import { useEffect, useState } from "react";
import { Outlet, useNavigate, Link, useLocation } from "react-router-dom";
import { setStudentToken, studentApi, type StudentProfile } from "../api";
import GoogleSignInButton from "./GoogleSignInButton";
import ThemeToggle from "./ThemeToggle";

export default function StudentLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const [student, setStudent] = useState<StudentProfile | null>(null);
  const [linkError, setLinkError] = useState<string | null>(null);
  // Only has any visible effect below the 760px breakpoint (see index.css)
  // — on desktop the sidebar's full contents are always shown regardless
  // of this state, so there's no need to reset it on navigation there.
  const [navOpen, setNavOpen] = useState(false);

  useEffect(() => {
    studentApi.me().then(setStudent).catch(() => {});
  }, []);

  // Closes the collapsed nav after following a link — otherwise it stays
  // open over the new page's content on a phone, since navigating doesn't
  // remount this layout.
  useEffect(() => {
    setNavOpen(false);
  }, [location.pathname]);

  async function handleLinkGoogle(idToken: string) {
    setLinkError(null);
    try {
      await studentApi.linkGoogleAccount(idToken);
      const me = await studentApi.me();
      setStudent(me);
    } catch (err) {
      setLinkError(err instanceof Error ? err.message : "Couldn't link that Google account");
    }
  }

  function logout() {
    setStudentToken(null);
    navigate("/login");
  }

  return (
    <div className="app-shell">
      <nav className={`sidebar${navOpen ? " sidebar-open" : ""}`}>
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 8 }}>
          <img src="/logo-tight.png?v=3" alt="Skoolgpt" className="sidebar-logo" />
          <div style={{ display: "flex", gap: 8 }}>
            <ThemeToggle />
            {/* Only rendered/visible below the 760px breakpoint (see
                index.css's .sidebar-toggle) — on desktop this sits in the
                DOM but display:none, so no extra conditional needed here. */}
            <button
              className="sidebar-toggle"
              onClick={() => setNavOpen((open) => !open)}
              aria-label={navOpen ? "Close menu" : "Open menu"}
              aria-expanded={navOpen}
            >
              {navOpen ? "✕" : "☰"}
            </button>
          </div>
        </div>
        <div className="sidebar-collapsible">
          {student && (
            <div className="sidebar-user">
              <div className="photo-preview" style={{ width: 36, height: 36 }}>
                <span className="photo-placeholder" style={{ fontSize: 14 }}>{student.name.charAt(0)}</span>
              </div>
              <div>
                <div className="sidebar-user-name">{student.name}</div>
                <div className="sidebar-user-role">₹{student.credit_balance.toFixed(2)} credits</div>
              </div>
            </div>
          )}
          <div className="nav-group">
            <span className="nav-group-label">Learning</span>
            <Link
              to="/chat"
              className={location.pathname === "/chat" ? "active" : undefined}
              style={{ display: "block", padding: "10px 14px", borderRadius: 10 }}
            >
              AI Tutor Chat
            </Link>
            {/* Voice calling can't run on WhatsApp (no two-way real-time call
                support in the Business API) — this is the portal page that
                exists purely because of that gap; see backend
                app.routers.voice_call's module docstring. */}
            <Link
              to="/call"
              className={location.pathname === "/call" ? "active" : undefined}
              style={{ display: "block", padding: "10px 14px", borderRadius: 10 }}
            >
              🎙️ Talk out loud
            </Link>
          </div>
          {student && (
            <div className="nav-group">
              <span className="nav-group-label">Account</span>
              {student.email ? (
                <p className="muted" style={{ fontSize: 12, padding: "0 14px" }}>✅ Google linked ({student.email})</p>
              ) : (
                <div style={{ padding: "0 14px" }}>
                  {/* Sidebar is 232px wide with 18px side padding (see
                      .sidebar in index.css) — 196px of real inner width.
                      Confirmed live the previous 200px value overflowed
                      past the sidebar's left edge. */}
                  <GoogleSignInButton onCredential={handleLinkGoogle} text="continue_with" width="180" />
                  {linkError && <p className="error" style={{ fontSize: 12 }}>{linkError}</p>}
                </div>
              )}
            </div>
          )}
          <button className="logout" onClick={logout}>
            Sign Out
          </button>
        </div>
      </nav>
      <main className="content">
        <Outlet context={{ student, setStudent }} />
      </main>
    </div>
  );
}
