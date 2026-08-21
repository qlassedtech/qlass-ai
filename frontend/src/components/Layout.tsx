import { useEffect, useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { api, absoluteUrl, setToken, type Teacher } from "../api";
import ThemeToggle from "./ThemeToggle";

export default function Layout() {
  const navigate = useNavigate();
  const [teacher, setTeacher] = useState<Teacher | null>(null);
  // Only set for a teacher/admin belonging to exactly one school (see
  // belongsToOneSchool below) — org_admin/super_admin manage many schools
  // at once, so there's no single school identity to show instead of
  // Qlass's own. Matches the promise made on the public landing page
  // (Join.tsx's "Your School's Own Branded Portal") — this portal is where
  // that promise was never actually implemented.
  const [schoolBranding, setSchoolBranding] = useState<{ name: string; logo_url: string | null } | null>(null);

  useEffect(() => {
    api.me().then(setTeacher).catch(() => {});
  }, []);

  const belongsToOneSchool = teacher?.role === "admin" || teacher?.role === "teacher";

  useEffect(() => {
    if (!belongsToOneSchool) {
      setSchoolBranding(null);
      return;
    }
    function refetch() {
      api.getSchool().then((school) => setSchoolBranding({ name: school.name, logo_url: school.logo_url })).catch(() => {});
    }
    refetch();
    // Layout stays mounted across route navigation (it wraps every admin
    // route via <Outlet/>), so without this, uploading a new logo from
    // SchoolProfile — which only updates ITS OWN local state — never
    // reaches the sidebar until a hard page reload. See the matching
    // dispatch in SchoolProfile.tsx's upload handler.
    window.addEventListener("school-branding-updated", refetch);
    return () => window.removeEventListener("school-branding-updated", refetch);
  }, [belongsToOneSchool]);

  function logout() {
    setToken(null);
    navigate("/login");
  }

  const canManageSchool =
    teacher?.role === "admin" || teacher?.role === "org_admin" || teacher?.role === "super_admin";
  const canUseTeachingTools = teacher?.role === "teacher" || teacher?.role === "admin";
  // A single school's own profile/branding doesn't apply to org_admin or
  // super_admin, who each manage many schools at once (see "Schools & Sales"
  // below instead) — GET /admin/school 400s for either role since neither
  // has a single centre_id, which is exactly what surfaced this as a real
  // bug: the link used to show for super_admin and the page hung on
  // "Loading..." forever once the request failed.
  const hasOneOwnSchool = teacher?.role === "admin";
  const managesMultipleSchools = teacher?.role === "org_admin" || teacher?.role === "super_admin";

  return (
    <div className="app-shell">
      <nav className="sidebar">
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 8 }}>
          {schoolBranding ? (
            <div className="sidebar-school-brand">
              {schoolBranding.logo_url ? (
                <img src={absoluteUrl(schoolBranding.logo_url) || undefined} alt={schoolBranding.name} className="sidebar-school-logo" />
              ) : (
                <span className="sidebar-school-logo-placeholder" aria-hidden="true">🏫</span>
              )}
              <div>
                <div className="sidebar-school-name">{schoolBranding.name}</div>
                <div className="sidebar-school-powered-by">Powered by Qlass</div>
              </div>
            </div>
          ) : (
            <img src="/logo-tight.png" alt="Qlass Learning" className="sidebar-logo" />
          )}
          <ThemeToggle />
        </div>

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
              <NavLink to="/assign-quiz" className={({ isActive }) => (isActive ? "active" : "")}>
                Assign Quiz
              </NavLink>
            </>
          )}
          <NavLink to="/my-tutor" className={({ isActive }) => (isActive ? "active" : "")}>
            My AI Tutor
          </NavLink>
          <NavLink to="/credits" className={({ isActive }) => (isActive ? "active" : "")}>
            Billing & Credits
          </NavLink>
          <NavLink to="/my-account" className={({ isActive }) => (isActive ? "active" : "")}>
            My Account
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
            {hasOneOwnSchool && (
              <NavLink to="/school-profile" className={({ isActive }) => (isActive ? "active" : "")}>
                School Profile
              </NavLink>
            )}
            {managesMultipleSchools && (
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
