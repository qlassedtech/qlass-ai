import { useEffect, useState } from "react";
import ChatWindow from "../components/ChatWindow";
import { api } from "../api";

declare global {
  interface Window {
    Razorpay: new (options: Record<string, unknown>) => { open: () => void };
  }
}

function loadRazorpayScript(): Promise<void> {
  return new Promise((resolve, reject) => {
    if (window.Razorpay) return resolve();
    const script = document.createElement("script");
    script.src = "https://checkout.razorpay.com/v1/checkout.js";
    script.onload = () => resolve();
    script.onerror = () => reject(new Error("Couldn't load the payment widget — check your connection and retry"));
    document.body.appendChild(script);
  });
}

export default function MyTutor() {
  const [balance, setBalance] = useState<number | null>(null);
  const [subscriptionPlan, setSubscriptionPlan] = useState<string>("credits");
  const [subscriptionExpiresAt, setSubscriptionExpiresAt] = useState<string | null>(null);
  const [autoRenewing, setAutoRenewing] = useState(false);
  const [subLoading, setSubLoading] = useState(false);
  const [subStatus, setSubStatus] = useState<string | null>(null);

  function loadProfile() {
    api.getMyTutor().then((t) => {
      setBalance(t.credit_balance);
      setSubscriptionPlan(t.subscription_plan);
      setSubscriptionExpiresAt(t.subscription_expires_at);
      setAutoRenewing(t.auto_renewing);
    });
  }

  useEffect(loadProfile, []);

  async function handleSubscribe() {
    setSubStatus(null);
    setSubLoading(true);
    try {
      await loadRazorpayScript();
      const subscription = await api.createMyTutorSubscription();

      const razorpay = new window.Razorpay({
        key: subscription.key_id,
        subscription_id: subscription.subscription_id,
        name: "Qlass Learning",
        description: "My AI Tutor — ₹3500/month, auto-renews",
        handler: async (response: {
          razorpay_subscription_id: string;
          razorpay_payment_id: string;
          razorpay_signature: string;
        }) => {
          try {
            await api.verifyMyTutorSubscription(response);
            setSubStatus("Subscribed! Auto-renews every month.");
            loadProfile();
          } catch (err) {
            setSubStatus(err instanceof Error ? err.message : "Payment went through but we couldn't confirm it — contact Qlass support");
          }
        },
        modal: { ondismiss: () => setSubLoading(false) },
        theme: { color: "#2b3ec4" },
      });
      razorpay.open();
    } catch (err) {
      setSubStatus(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setSubLoading(false);
    }
  }

  async function handleCancel() {
    setSubStatus(null);
    setSubLoading(true);
    try {
      const result = await api.cancelMyTutorSubscription();
      const until = result.access_until ? new Date(result.access_until).toLocaleDateString() : "";
      setSubStatus(`Auto-renewal cancelled — you'll keep unlimited access until ${until}.`);
      loadProfile();
    } catch (err) {
      setSubStatus(err instanceof Error ? err.message : "Failed to cancel");
    } finally {
      setSubLoading(false);
    }
  }

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>My AI Tutor</h1>
          <p>Your own personal tutor account, separate from any student's — ask anything, send a photo of a question, record a voice note, or share a PDF</p>
        </div>
        {subscriptionPlan !== "unlimited" && balance !== null && <p className="muted">₹{balance.toFixed(2)} credits</p>}
      </div>

      <div className="card" style={{ marginBottom: 20 }}>
        {subscriptionPlan === "unlimited" ? (
          <>
            <p style={{ marginBottom: 8 }}>
              🎓 <strong>Unlimited plan active</strong>
              {subscriptionExpiresAt && <> — until {new Date(subscriptionExpiresAt).toLocaleDateString()}</>}
              {autoRenewing && <span className="muted"> (auto-renews)</span>}
            </p>
            {autoRenewing && (
              <button type="button" onClick={handleCancel} disabled={subLoading}>
                Cancel Auto-Renewal
              </button>
            )}
          </>
        ) : (
          <>
            <p className="muted" style={{ marginBottom: 10 }}>
              Go unlimited for ₹3500/month, auto-renews — no more topping up your personal tutor wallet.
            </p>
            <button type="button" onClick={handleSubscribe} disabled={subLoading}>
              {subLoading ? "Please wait…" : "Subscribe for ₹3500/month"}
            </button>
          </>
        )}
        {subStatus && <p className="status" style={{ marginTop: 8 }}>{subStatus}</p>}
      </div>

      <ChatWindow
        fetchHistory={api.getMyTutorHistory}
        sendMessage={api.sendMyTutorMessage}
        onSendImage={api.sendMyTutorImage}
        onSendVoice={api.sendMyTutorVoice}
        onSendDocument={api.sendMyTutorDocument}
        onBalanceChange={setBalance}
      />
    </div>
  );
}
