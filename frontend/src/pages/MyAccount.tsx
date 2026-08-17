import { useEffect, useState } from "react";
import { api, type Teacher } from "../api";
import GoogleSignInButton from "../components/GoogleSignInButton";

export default function MyAccount() {
  const [teacher, setTeacher] = useState<Teacher | null>(null);
  const [newPhone, setNewPhone] = useState("");
  const [otp, setOtp] = useState("");
  // Set once requestChangePhone sends a code — the form then asks for it
  // instead of submitting the number again. false when the number turned
  // out not to be on WhatsApp (see requestChangePhone's own docstring on
  // the backend) — the change already went through, no code needed.
  const [phoneOtpRequired, setPhoneOtpRequired] = useState<boolean | null>(null);
  const [phoneError, setPhoneError] = useState<string | null>(null);
  const [phoneLoading, setPhoneLoading] = useState(false);
  const [emailError, setEmailError] = useState<string | null>(null);
  const [emailLoading, setEmailLoading] = useState(false);
  const [savedNotice, setSavedNotice] = useState<string | null>(null);

  useEffect(() => {
    api.me().then(setTeacher).catch(() => {});
  }, []);

  async function handlePhoneSubmit(e: React.FormEvent) {
    e.preventDefault();
    setPhoneError(null);
    setPhoneLoading(true);
    try {
      const { otp_required } = await api.requestChangePhone(newPhone);
      if (otp_required) {
        setPhoneOtpRequired(true);
        return;
      }
      // Not a WhatsApp number — already applied, no code needed.
      const { phone } = await api.requestChangePhoneVerify(newPhone);
      setTeacher((t) => (t ? { ...t, phone, phone_verified: true } : t));
      setNewPhone("");
      setPhoneOtpRequired(null);
      setSavedNotice("Phone number updated.");
    } catch (err) {
      setPhoneError(err instanceof Error ? err.message : "Couldn't send a verification code — please try again");
    } finally {
      setPhoneLoading(false);
    }
  }

  async function handlePhoneOtpSubmit(e: React.FormEvent) {
    e.preventDefault();
    setPhoneError(null);
    setPhoneLoading(true);
    try {
      const { phone } = await api.requestChangePhoneVerify(newPhone, otp);
      setTeacher((t) => (t ? { ...t, phone, phone_verified: true } : t));
      setNewPhone("");
      setOtp("");
      setPhoneOtpRequired(null);
      setSavedNotice("Phone number updated.");
    } catch (err) {
      setPhoneError(err instanceof Error ? err.message : "Incorrect code — please try again");
    } finally {
      setPhoneLoading(false);
    }
  }

  async function handleGoogleCredential(idToken: string) {
    setEmailError(null);
    setEmailLoading(true);
    try {
      const { email } = await api.changeEmail(idToken);
      setTeacher((t) => (t ? { ...t, email } : t));
      setSavedNotice("Email updated.");
    } catch (err) {
      setEmailError(err instanceof Error ? err.message : "Couldn't link that Google account — please try again");
    } finally {
      setEmailLoading(false);
    }
  }

  if (!teacher) return null;

  return (
    <div>
      <h1>My Account</h1>
      <p className="muted">Manage the phone number and email linked to your login</p>

      {savedNotice && <p className="success">{savedNotice}</p>}

      <div className="card" style={{ maxWidth: 480, marginTop: 16 }}>
        <h2 style={{ marginTop: 0 }}>Phone number</h2>
        <p className="muted" style={{ fontSize: 13 }}>
          Current: {teacher.phone} {teacher.phone_verified ? "✅ WhatsApp-verified" : "⚠️ Not WhatsApp-verified"}
        </p>
        {!teacher.phone_verified && (
          <p className="muted" style={{ fontSize: 13 }}>
            This number was never proven to belong to you — WhatsApp OTP login isn't available until you verify it
            here.
          </p>
        )}

        {phoneOtpRequired ? (
          <form onSubmit={handlePhoneOtpSubmit}>
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
            {phoneError && <p className="error">{phoneError}</p>}
            <button type="submit" disabled={phoneLoading}>
              {phoneLoading ? "Please wait..." : "Verify & Save"}
            </button>
            <p className="auth-links">
              <a
                href="#"
                onClick={(e) => {
                  e.preventDefault();
                  setPhoneOtpRequired(null);
                  setPhoneError(null);
                }}
              >
                Wrong number? Go back
              </a>
            </p>
          </form>
        ) : (
          <form onSubmit={handlePhoneSubmit}>
            <label>
              New WhatsApp number
              <input
                value={newPhone}
                onChange={(e) => setNewPhone(e.target.value)}
                placeholder="91XXXXXXXXXX"
                required
              />
            </label>
            {phoneError && <p className="error">{phoneError}</p>}
            <button type="submit" disabled={phoneLoading}>
              {phoneLoading ? "Please wait..." : "Change Phone Number"}
            </button>
          </form>
        )}
      </div>

      <div className="card" style={{ maxWidth: 480, marginTop: 16 }}>
        <h2 style={{ marginTop: 0 }}>Email</h2>
        <p className="muted" style={{ fontSize: 13 }}>
          Current: {teacher.email || "No email linked"}
        </p>
        <p className="muted" style={{ fontSize: 13 }}>
          Link (or replace) the Google account you sign in with — this also lets you sign in with Google alongside
          your password.
        </p>
        {emailError && <p className="error">{emailError}</p>}
        <div style={{ opacity: emailLoading ? 0.6 : 1, pointerEvents: emailLoading ? "none" : "auto" }}>
          <GoogleSignInButton text="continue_with" onCredential={handleGoogleCredential} />
        </div>
      </div>
    </div>
  );
}
