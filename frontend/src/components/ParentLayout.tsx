import { useNavigate } from "react-router-dom";
import { setParentToken } from "../api";
import ThemeToggle from "./ThemeToggle";

export default function ParentLayout({ children }: { children: React.ReactNode }) {
  const navigate = useNavigate();

  function logout() {
    setParentToken(null);
    navigate("/login");
  }

  return (
    <div className="center-page" style={{ alignItems: "flex-start", paddingTop: 60 }}>
      <div className="card" style={{ width: 480 }}>
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 8 }}>
          <img src="/logo-tight.png?v=3" alt="Skoolgpt" className="login-logo" style={{ margin: 0 }} />
          <ThemeToggle />
        </div>
        {children}
        <button className="logout" onClick={logout} style={{ marginTop: 20, width: "100%" }}>
          Sign Out
        </button>
      </div>
    </div>
  );
}
