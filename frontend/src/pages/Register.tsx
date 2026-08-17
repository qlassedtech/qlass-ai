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
  // Set once the initial form is submitted and a WhatsApp OTP has been
  // sent — the form then switches to asking for that code instead of
  // creating the school+admin account immediately (see api.ts's
  // registerSchool vs registerSchoolVerify). Proves the admin actually
  // controls admin_phone before a fully-privileged account is created for it.
  const [otpRequired, setOtpRequired] = useState(false);
  const [otp, setOtp] = useState("");
  const navigate = useNavigate();

  async function finishRegistration(enteredOtp?: string) {
    const { access_token } = await api.registerSchoolVerify({
      school_name: schoolName,
      city: city || undefined,
      board: board || undefined,
      admin_name: adminName,
      admin_phone: adminPhone,
      password: googleIdToken ? undefined : password,
      google_id_token: googleIdToken || undefined,
      otp: enteredOtp,
    });
    setToken(access_token);
    navigate("/students");
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const { otp_required } = await api.registerSchool({
        school_name: schoolName,
        city: city || undefined,
        board: board || undefined,
        admin_name: adminName,
        admin_phone: adminPhone,
        password: googleIdToken ? undefined : password,
        google_id_token: googleIdToken || undefined,
      });
      if (otp_required) {
        setOtpRequired(true);
        return;
      }
      // Google sign-in, or a number that isn't on WhatsApp — no code to
      // enter, step 2 can run immediately.
      await finishRegistration();
    } catch (err) {
      setError(err instanceof Error ? err.message : "We couldn't register your school — please try again");
    } finally {
      setLoading(false);
    }
  }

  async function handleVerifyOtp(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await finishRegistration(otp);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Incorrect code — please try again");
    } finally {
      setLoading(false);
    }
  }

  if (otpRequired) {
    return (
      <div className="center-page">
        <form className="card" onSubmit={handleVerifyOtp}>
          <img src="/logo-tight.png" alt="Qlass Learning" className="login-logo" />
          <h1>Verify Your WhatsApp Number</h1>
          <p className="login-subtitle">We sent a code to {adminPhone} on WhatsApp — enter it below to finish setting up your school.</p>
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
            {loading ? "Please wait..." : "Verify & Create School Console"}
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
      </div>
    );
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
          {loading ? "Sending code..." : "Create School Console"}
        </button>
        <p className="auth-links">
          <Link to="/login">Already have an account? Sign in</Link>
          {/* Someone landing here as a STUDENT (wrong link, or a parent
              rather than an admin) had no way back to the page meant for
              them — this form only makes sense for someone registering a
              whole school. */}
          <Link to="/join">Are you a student? Sign up here</Link>
        </p>
      </form>
    </div>
  );
}
