import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";

export default function ForgotPassword() {
  const [phone, setPhone] = useState("");
  const [otp, setOtp] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [otpSent, setOtpSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();

  async function handleSendOtp(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await api.forgotPassword(phone);
      setOtpSent(true);
      setStatus("If that number has an account, we've sent a reset code over WhatsApp.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong — please try again");
    } finally {
      setLoading(false);
    }
  }

  async function handleReset(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await api.resetPassword(phone, otp, newPassword);
      navigate("/login");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't reset your password — please check the code and try again");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="center-page">
      <form className="card" onSubmit={otpSent ? handleReset : handleSendOtp}>
        <img src="/logo-tight.png" alt="Skoolgpt" className="login-logo" />
        <h1>Reset Password</h1>
        <p className="login-subtitle">
          {otpSent ? "Enter the code we sent over WhatsApp" : "We'll send a reset code to your WhatsApp number"}
        </p>
        <label>
          Registered phone number
          <input
            value={phone}
            onChange={(e) => setPhone(e.target.value)}
            placeholder="91XXXXXXXXXX"
            required
            disabled={otpSent}
          />
        </label>
        {otpSent && (
          <>
            <label>
              6-digit code
              <input value={otp} onChange={(e) => setOtp(e.target.value)} required maxLength={6} />
            </label>
            <label>
              New password
              <input
                type="password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                required
                minLength={6}
              />
            </label>
          </>
        )}
        {status && !error && <p className="status">{status}</p>}
        {error && <p className="error">{error}</p>}
        <button type="submit" disabled={loading}>
          {loading ? "Please wait..." : otpSent ? "Reset Password" : "Send Reset Code"}
        </button>
        <p className="auth-links">
          <Link to="/login">Back to sign in</Link>
        </p>
      </form>
    </div>
  );
}
