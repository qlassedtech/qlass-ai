import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { absoluteUrl, api, normalizePhone, parentApi, publicApi, setParentToken, setStudentToken, setToken } from "../api";
import GoogleSignInButton from "../components/GoogleSignInButton";
import ThemeToggle from "../components/ThemeToggle";

type Step = "phone" | "password" | "otp" | "parent_otp" | "teacher_otp" | "student_password";

export default function Login() {
  const [searchParams] = useSearchParams();
  const schoolSlug = searchParams.get("school") || undefined;
  const [schoolName, setSchoolName] = useState<string | null>(null);
  const [schoolLogo, setSchoolLogo] = useState<string | null>(null);
  const [step, setStep] = useState<Step>("phone");
  const [phone, setPhone] = useState("");
  const [password, setPassword] = useState("");
  const [otp, setOtp] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();

  useEffect(() => {
    if (schoolSlug) {
      publicApi.schoolInfo(schoolSlug).then((res) => {
        setSchoolName(res.name);
        setSchoolLogo(res.logo_url);
      });
    }
  }, [schoolSlug]);

  async function handlePhoneSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    const normalized = normalizePhone(phone);
    setPhone(normalized);
    try {
      const { login_type } = await api.checkPhone(normalized);
      if (login_type === "password") {
        setStep("password");
      } else if (login_type === "student_password") {
        setStep("student_password");
      } else if (login_type === "parent_otp") {
        await parentApi.requestOtp(normalized);
        setStep("parent_otp");
      } else {
        await api.requestStudentOtp(normalized);
        setStep("otp");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong — please try again");
    } finally {
      setLoading(false);
    }
  }

  async function handlePasswordSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const { access_token } = await api.login(phone, password);
      setToken(access_token);
      // An organization admin (e.g. a government programme spanning many
      // schools) manages a portfolio of schools, not one school's own
      // roster — land them on the schools overview instead of the
      // single-school "Student Roster" default.
      const me = await api.me();
      navigate(me.role === "org_admin" ? "/schools" : "/students");
    } catch (err) {
      setError(err instanceof Error ? err.message : "We couldn't sign you in — please try again");
    } finally {
      setLoading(false);
    }
  }

  async function handleStudentPasswordSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const { access_token } = await api.studentLogin(phone, password);
      setStudentToken(access_token);
      navigate("/chat");
    } catch (err) {
      setError(err instanceof Error ? err.message : "We couldn't sign you in — please try again");
    } finally {
      setLoading(false);
    }
  }

  async function handleGoogleCredential(idToken: string) {
    setError(null);
    setLoading(true);
    // Tried in this order (teacher/admin, then student) since a Google
    // credential alone doesn't say which kind of account it's linked to —
    // same priority as check_phone's own "teacher login always takes
    // priority" comment on the backend.
    try {
      const { access_token } = await api.googleLogin(idToken);
      setToken(access_token);
      const me = await api.me();
      navigate(me.role === "org_admin" ? "/schools" : "/students");
      return;
    } catch {
      // fall through to the student attempt below
    } finally {
      setLoading(false);
    }
    setLoading(true);
    try {
      const { access_token } = await api.studentGoogleLogin(idToken);
      setStudentToken(access_token);
      navigate("/chat");
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "No account is linked to this Google account yet — sign in with your phone number and link Google from your account settings",
      );
    } finally {
      setLoading(false);
    }
  }

  async function handleTeacherOtpRequest() {
    setError(null);
    setLoading(true);
    try {
      await api.requestTeacherOtp(phone);
      setStep("teacher_otp");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't send a code — please try again");
    } finally {
      setLoading(false);
    }
  }

  async function handleTeacherOtpSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const { access_token } = await api.verifyTeacherOtp(phone, otp);
      setToken(access_token);
      const me = await api.me();
      navigate(me.role === "org_admin" ? "/schools" : "/students");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't verify that code — please try again");
    } finally {
      setLoading(false);
    }
  }

  async function handleOtpSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      // No name collected here anymore — a genuinely new student's name
      // (and class/board/school) is asked naturally by the chat's own
      // onboarding flow on their first real message instead, same as
      // every WhatsApp-native first contact already works.
      const { access_token } = await api.verifyStudentOtp(phone, otp);
      setStudentToken(access_token);
      navigate("/chat");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't verify that code — please try again");
    } finally {
      setLoading(false);
    }
  }

  async function handleParentOtpSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const { access_token } = await parentApi.verifyOtp(phone, otp);
      setParentToken(access_token);
      navigate("/parent");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't verify that code — please try again");
    } finally {
      setLoading(false);
    }
  }

  const submitHandlers: Record<Step, (e: React.FormEvent) => void> = {
    phone: handlePhoneSubmit,
    password: handlePasswordSubmit,
    student_password: handleStudentPasswordSubmit,
    otp: handleOtpSubmit,
    parent_otp: handleParentOtpSubmit,
    teacher_otp: handleTeacherOtpSubmit,
  };

  return (
    <div className="center-page">
      <div style={{ position: "absolute", top: 20, right: 20, zIndex: 2 }}>
        <ThemeToggle />
      </div>
      {/* .center-page centers its children as a flex ROW — without this
          wrapper, the "Powered by" badge below sits as a second row item
          beside the card instead of stacked under it, shoving the card off
          true center. Wrapping both in one column container makes the pair
          center as a single unit, badge correctly stacked underneath. */}
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", position: "relative", zIndex: 1 }}>
      <form className="card" onSubmit={submitHandlers[step]}>
        {schoolName ? (
          <div className="landing-school-logo-large" style={{ width: 96, height: 96, margin: "0 auto 16px" }}>
            {schoolLogo ? (
              <img src={absoluteUrl(schoolLogo) || undefined} alt={schoolName} />
            ) : (
              <span className="logo-placeholder" aria-hidden="true" style={{ fontSize: 40 }}>🏫</span>
            )}
          </div>
        ) : (
          <img src="/logo-tight.png?v=3" alt="Skoolgpt" className="login-logo" />
        )}
        <h1>{schoolName ? `Welcome back, ${schoolName}` : "Welcome to Skoolgpt"}</h1>
        {step !== "phone" && (
          <p className="login-subtitle">
            {(step === "password" || step === "student_password") && "Enter your password"}
            {(step === "otp" || step === "parent_otp" || step === "teacher_otp") && "Enter the code"}
          </p>
        )}

        {step === "phone" && (
          <label>
            WhatsApp number
            <input value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="91XXXXXXXXXX" required />
          </label>
        )}

        {(step === "password" || step === "student_password") && (
          <>
            <p className="muted" style={{ marginTop: -8, marginBottom: 12, fontSize: 13 }}>{phone}</p>
            <label>
              Password
              <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
            </label>
          </>
        )}

        {(step === "otp" || step === "parent_otp" || step === "teacher_otp") && (
          <>
            <p className="muted" style={{ marginTop: -8, marginBottom: 12, fontSize: 13 }}>{phone}</p>
            <label>
              6-digit code
              <input value={otp} onChange={(e) => setOtp(e.target.value)} required maxLength={6} />
            </label>
          </>
        )}

        {error && <p className="error">{error}</p>}
        <button type="submit" disabled={loading}>
          {loading
            ? "Please wait..."
            : step === "phone"
              ? "Continue"
              : step === "password" || step === "student_password"
                ? "Sign In"
                : "Verify & Continue"}
        </button>

        {step === "password" && (
          <button type="button" className="button-secondary" disabled={loading} onClick={handleTeacherOtpRequest}>
            Login with WhatsApp OTP instead
          </button>
        )}

        {step === "phone" && (
          <>
            <p className="auth-divider">or</p>
            <GoogleSignInButton onCredential={handleGoogleCredential} text="signin_with" />
          </>
        )}

        {step !== "phone" && (
          <p className="auth-links">
            <a
              href="#"
              onClick={(e) => {
                e.preventDefault();
                setStep("phone");
                setError(null);
              }}
            >
              Use a different number
            </a>
          </p>
        )}
        {step === "phone" && (
          <div className="auth-links">
            <Link to="/forgot-password">Forgot password?</Link>
            <Link to={schoolSlug ? `/join?school=${schoolSlug}` : "/join"}>New here? Sign up</Link>
          </div>
        )}
      </form>
      {schoolName && (
        <div className="landing-powered-by">
          <span>Powered by</span>
          <img src="/logo-tight.png?v=3" alt="Skoolgpt" />
        </div>
      )}
      </div>
    </div>
  );
}
