import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, parentApi, setParentToken, setStudentToken, setToken } from "../api";
import ThemeToggle from "../components/ThemeToggle";

type Step = "phone" | "password" | "otp" | "parent_otp";

export default function Login() {
  const [step, setStep] = useState<Step>("phone");
  const [phone, setPhone] = useState("");
  const [password, setPassword] = useState("");
  const [otp, setOtp] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();

  async function handlePhoneSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const { login_type } = await api.checkPhone(phone);
      if (login_type === "password") {
        setStep("password");
      } else if (login_type === "parent_otp") {
        await parentApi.requestOtp(phone);
        setStep("parent_otp");
      } else {
        await api.requestStudentOtp(phone);
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
      navigate("/students");
    } catch (err) {
      setError(err instanceof Error ? err.message : "We couldn't sign you in — please try again");
    } finally {
      setLoading(false);
    }
  }

  async function handleOtpSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const { access_token } = await api.verifyStudentOtp(phone, otp, name || undefined);
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
    otp: handleOtpSubmit,
    parent_otp: handleParentOtpSubmit,
  };

  return (
    <div className="center-page">
      <div style={{ position: "absolute", top: 20, right: 20, zIndex: 2 }}>
        <ThemeToggle />
      </div>
      <form className="card" onSubmit={submitHandlers[step]}>
        <img src="/logo.jpeg" alt="Qlass Learning" className="login-logo" />
        <h1>Welcome to Qlass</h1>
        <p className="login-subtitle">
          {step === "phone" && "Sign in as a school, teacher, parent, or student"}
          {step === "password" && "Enter your portal password"}
          {step === "otp" && "Enter the code we sent over WhatsApp"}
          {step === "parent_otp" && "Enter the code we sent over WhatsApp"}
        </p>

        {step === "phone" && (
          <label>
            WhatsApp number
            <input value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="91XXXXXXXXXX" required />
          </label>
        )}

        {step === "password" && (
          <>
            <p className="muted" style={{ marginTop: -8, marginBottom: 12, fontSize: 13 }}>{phone}</p>
            <label>
              Password
              <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
            </label>
          </>
        )}

        {(step === "otp" || step === "parent_otp") && (
          <>
            <p className="muted" style={{ marginTop: -8, marginBottom: 12, fontSize: 13 }}>{phone}</p>
            <label>
              6-digit code
              <input value={otp} onChange={(e) => setOtp(e.target.value)} required maxLength={6} />
            </label>
            {step === "otp" && (
              <label>
                Your name (first time only)
                <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Optional" />
              </label>
            )}
          </>
        )}

        {error && <p className="error">{error}</p>}
        <button type="submit" disabled={loading}>
          {loading
            ? "Please wait..."
            : step === "phone"
              ? "Continue"
              : step === "password"
                ? "Sign In"
                : "Verify & Continue"}
        </button>

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
          <p className="auth-links">
            <Link to="/forgot-password">Forgot password?</Link>
            <span> · </span>
            <Link to="/register">Register your school</Link>
          </p>
        )}
      </form>
    </div>
  );
}
