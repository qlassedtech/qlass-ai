import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, setToken } from "../api";
import GoogleSignInButton from "../components/GoogleSignInButton";

// Same list used across AssignQuiz/Presentations/StudentDetail/Workbook —
// kept in sync manually since there's no shared constants module yet.
const BOARD_OPTIONS = ["CBSE", "ICSE", "BSEB", "State Board"];

export default function Register() {
  const [schoolName, setSchoolName] = useState("");
  const [city, setCity] = useState("");
  // Confirmed live (real school onboarding): with no board set at
  // registration, every student joining through this school's /join link
  // got asked "which board?" in chat, even though a school always already
  // knows its own board — this closes that gap at signup time.
  const [board, setBoard] = useState("");
  const [adminName, setAdminName] = useState("");
  const [adminPhone, setAdminPhone] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  // Once a Google credential comes back, the password field is no longer
  // required — school_name/city/admin_name/admin_phone are still needed
  // either way (Google only supplies identity/email, not those details).
  const [googleIdToken, setGoogleIdToken] = useState<string | null>(null);
  const navigate = useNavigate();

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const { access_token } = await api.registerSchool({
        school_name: schoolName,
        city: city || undefined,
        board: board || undefined,
        admin_name: adminName,
        admin_phone: adminPhone,
        password: googleIdToken ? undefined : password,
        google_id_token: googleIdToken || undefined,
      });
      setToken(access_token);
      navigate("/students");
    } catch (err) {
      setError(err instanceof Error ? err.message : "We couldn't register your school — please try again");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="center-page">
      <form className="card" onSubmit={handleSubmit}>
        <img src="/logo-tight.png" alt="Qlass Learning" className="login-logo" />
        <h1>Register Your School</h1>
        <p className="login-subtitle">Create your school's console and admin account</p>
        <label>
          School name
          <input value={schoolName} onChange={(e) => setSchoolName(e.target.value)} required />
        </label>
        <label>
          City (optional)
          <input value={city} onChange={(e) => setCity(e.target.value)} />
        </label>
        <label>
          Board
          <select value={board} onChange={(e) => setBoard(e.target.value)} required>
            <option value="" disabled>
              Select your school's board
            </option>
            {BOARD_OPTIONS.map((b) => (
              <option key={b} value={b}>
                {b}
              </option>
            ))}
          </select>
        </label>
        <label>
          Your name
          <input value={adminName} onChange={(e) => setAdminName(e.target.value)} required />
        </label>
        <label>
          Your WhatsApp number
          <input value={adminPhone} onChange={(e) => setAdminPhone(e.target.value)} placeholder="91XXXXXXXXXX" required />
        </label>
        {googleIdToken ? (
          <p className="muted" style={{ fontSize: 13 }}>
            ✅ Google account linked — you'll sign in with Google instead of a password.{" "}
            <a
              href="#"
              onClick={(e) => {
                e.preventDefault();
                setGoogleIdToken(null);
              }}
            >
              Use a password instead
            </a>
          </p>
        ) : (
          <>
            <label>
              Password
              <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required minLength={6} />
            </label>
            <p className="auth-divider">or</p>
            <GoogleSignInButton onCredential={setGoogleIdToken} text="signup_with" />
          </>
        )}
        {error && <p className="error">{error}</p>}
        <button type="submit" disabled={loading}>
          {loading ? "Creating account..." : "Create School Console"}
        </button>
        <p className="auth-links">
          <Link to="/login">Already have an account? Sign in</Link>
        </p>
      </form>
    </div>
  );
}
