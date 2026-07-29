import { useEffect, useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { api, absoluteUrl, setToken, type Teacher } from "../api";

export default function Layout() {
  const navigate = useNavigate();
  const [teacher, setTeacher] = useState<Teacher | null>(null);

  useEffect(() => {
    api.me().then(setTeacher).catch(() => {});
  }, []);

  function logout() {
    setToken(null);
    navigate("/login");
  }

  const canManageSchool = teacher?.role === "admin" || teacher?.role === "super_admin";
  const canUseTeachingTools = teacher?.role === "teacher" || teacher?.role === "admin";

  return (
    <div className="app-shell">
      <nav className="sidebar">
        <img src="/logo.jpeg" alt="Qlass Learning" className="sidebar-logo" />

        {teacher && (
          <div className="sidebar-user">
            <div className="photo-preview" style={{ width: 36, height: 36 }}>
              {teacher.photo_url ? (
                <img src={absoluteUrl(teacher.photo_url) || undefined} alt={teacher.name} />
              ) : (
                <span className="photo-placeholder" style={{ fontSize: 14 }}>{teacher.name.charAt(0)}</span>
              )}
            </div>
            <div>
              <div className="sidebar-user-name">{teacher.name}</div>
              <div className="sidebar-user-role">{teacher.role.replace("_", " ")}</div>
            </div>
          </div>
        )}

        <div className="nav-group">
          <span className="nav-group-label">Teaching</span>
          <NavLink to="/students" className={({ isActive }) => (isActive ? "active" : "")}>
            Student Roster
          </NavLink>
          <NavLink to="/bulk-upload" className={({ isActive }) => (isActive ? "active" : "")}>
            Bulk Enrollment
          </NavLink>
          {canUseTeachingTools && (
            <>
              <NavLink to="/workbook" className={({ isActive }) => (isActive ? "active" : "")}>
                Practice Worksheets
              </NavLink>
              <NavLink to="/presentations" className={({ isActive }) => (isActive ? "active" : "")}>
                Presentation Generator
              </NavLink>
            </>
          )}
          <NavLink to="/my-tutor" className={({ isActive }) => (isActive ? "active" : "")}>
            My AI Tutor
          </NavLink>
          <NavLink to="/credits" className={({ isActive }) => (isActive ? "active" : "")}>
            Billing & Credits
          </NavLink>
        </div>

        {canManageSchool && (
          <div className="nav-group">
            <span className="nav-group-label">School Administration</span>
            <NavLink to="/analytics" className={({ isActive }) => (isActive ? "active" : "")}>
              Analytics
            </NavLink>
            <NavLink to="/teachers" className={({ isActive }) => (isActive ? "active" : "")}>
              Teacher Accounts
            </NavLink>
            <NavLink to="/school-profile" className={({ isActive }) => (isActive ? "active" : "")}>
              School Profile
            </NavLink>
            {teacher?.role === "super_admin" && (
              <NavLink to="/schools" className={({ isActive }) => (isActive ? "active" : "")}>
                Schools & Sales
              </NavLink>
            )}
          </div>
        )}

        <button className="logout" onClick={logout}>
          Sign Out
        </button>
      </nav>
      <main className="content">
        <Outlet />
      </main>
    </div>
  );
}
