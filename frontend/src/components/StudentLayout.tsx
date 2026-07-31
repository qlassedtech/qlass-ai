import { useEffect, useState } from "react";
import { Outlet, useNavigate } from "react-router-dom";
import { setStudentToken, studentApi, type StudentProfile } from "../api";
import ThemeToggle from "./ThemeToggle";

export default function StudentLayout() {
  const navigate = useNavigate();
  const [student, setStudent] = useState<StudentProfile | null>(null);

  useEffect(() => {
    studentApi.me().then(setStudent).catch(() => {});
  }, []);

  function logout() {
    setStudentToken(null);
    navigate("/login");
  }

  return (
    <div className="app-shell">
      <nav className="sidebar">
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 8 }}>
          <img src="/logo.jpeg" alt="Qlass Learning" className="sidebar-logo" />
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
