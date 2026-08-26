import { useEffect, useState } from "react";
import { Outlet, useNavigate } from "react-router-dom";
import { setStudentToken, studentApi, type StudentProfile } from "../api";
import GoogleSignInButton from "./GoogleSignInButton";
import ThemeToggle from "./ThemeToggle";

export default function StudentLayout() {
  const navigate = useNavigate();
  const [student, setStudent] = useState<StudentProfile | null>(null);
  const [linkError, setLinkError] = useState<string | null>(null);

  useEffect(() => {
    studentApi.me().then(setStudent).catch(() => {});
  }, []);

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
      <nav className="sidebar">
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 8 }}>
          <img src="/logo-tight.png" alt="Skoolgpt" className="sidebar-logo" />
          <ThemeToggle />
        </div>
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
          <span className="active" style={{ display: "block", padding: "10px 14px", borderRadius: 10 }}>
            AI Tutor Chat
          </span>
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
      </nav>
      <main className="content">
        <Outlet context={{ student, setStudent }} />
      </main>
    </div>
  );
}
