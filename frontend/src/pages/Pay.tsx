import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { payApi } from "../api";

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

export default function Pay() {
  const [searchParams] = useSearchParams();
  const prefilledPhone = searchParams.get("phone") || "";
  const studentIdParam = searchParams.get("student_id");
  // Disambiguates a shared family phone with more than one child on it —
  // only meaningful when it arrived alongside a specific phone (a link a
  // teacher/parent generated already knows exactly which student it's
  // for); typed in manually with no student_id, the backend falls back to
  // its old lowest-id-on-this-phone behavior.
  const studentId = studentIdParam ? Number(studentIdParam) : undefined;
  const [phone, setPhone] = useState(prefilledPhone);
  const [amount, setAmount] = useState("");
  const [status, setStatus] = useState<{ kind: "error" | "success"; message: string } | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubscribe() {
    if (!phone) {
      setStatus({ kind: "error", message: "Enter the student's WhatsApp number first" });
      return;
    }
    setStatus(null);
    setLoading(true);
    try {
      await loadRazorpayScript();
      const subscription = await payApi.createSubscription(phone, studentId);

      const razorpay = new window.Razorpay({
        key: subscription.key_id,
        subscription_id: subscription.subscription_id,
        name: "Qlass Learning",
        description: "Unlimited AI Tutor plan — ₹2499/year, auto-renews",
        handler: async (response: {
          razorpay_subscription_id: string;
          razorpay_payment_id: string;
          razorpay_signature: string;
        }) => {
          try {
            const result = await payApi.verifySubscription({ ...response, phone, student_id: studentId });
            const expires = result.subscription_expires_at
              ? new Date(result.subscription_expires_at).toLocaleDateString()
              : "";
            setStatus({
              kind: "success",
              message: `Unlimited plan activated! Auto-renews — active until ${expires}.`,
            });
          } catch (err) {
            setStatus({
              kind: "error",
              message: err instanceof Error ? err.message : "Payment went through but we couldn't confirm it — contact Qlass support",
            });
          }
        },
        modal: { ondismiss: () => setLoading(false) },
        theme: { color: "#2b3ec4" },
      });
      razorpay.open();
    } catch (err) {
      setStatus({ kind: "error", message: err instanceof Error ? err.message : "Something went wrong" });
    } finally {
      setLoading(false);
    }
  }

  async function handlePay(e: React.FormEvent) {
    e.preventDefault();
    setStatus(null);
    setLoading(true);
    try {
      await loadRazorpayScript();
      const order = await payApi.createOrder(phone, Number(amount), studentId);

      const razorpay = new window.Razorpay({
        key: order.key_id,
        amount: order.amount,
        currency: order.currency,
        name: "Qlass Learning",
        description: "AI Tutor credit top-up",
        order_id: order.order_id,
        handler: async (response: {
          razorpay_order_id: string;
          razorpay_payment_id: string;
          razorpay_signature: string;
        }) => {
          try {
            const result = await payApi.verify({ ...response, phone, student_id: studentId });
            setStatus({
              kind: "success",
              message: `Payment successful — ₹${result.credited.toFixed(2)} added. New balance: ₹${result.balance.toFixed(2)}`,
            });
          } catch (err) {
            setStatus({
              kind: "error",
              message: err instanceof Error ? err.message : "Payment went through but we couldn't confirm it — contact Qlass support",
            });
          }
        },
        modal: { ondismiss: () => setLoading(false) },
        theme: { color: "#2b3ec4" },
      });
      razorpay.open();
    } catch (err) {
      setStatus({ kind: "error", message: err instanceof Error ? err.message : "Something went wrong" });
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="center-page">
      <div className="card">
        <img src="/logo-tight.png" alt="Qlass Learning" className="login-logo" />
        <h1>Top Up AI Credits</h1>
        <p className="login-subtitle">Add credits to your child's Qlass AI Tutor account on WhatsApp.</p>

        <form onSubmit={handlePay}>
          <label>
            Student's WhatsApp number
            <input
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
              placeholder="e.g. 91XXXXXXXXXX"
              required
              readOnly={!!prefilledPhone}
            />
          </label>
          <label>
            Amount (₹)
            <input
              type="number"
              min="10"
              step="1"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              placeholder="e.g. 100"
              required
            />
          </label>
          <button type="submit" disabled={loading}>
            {loading ? "Please wait…" : "Pay & Add Credits"}
          </button>
        </form>

        <div style={{ marginTop: 20, paddingTop: 20, borderTop: "1px solid var(--border)", textAlign: "center" }}>
          <p className="muted" style={{ fontSize: 13, marginBottom: 10 }}>
            Or go unlimited — ₹2499/year, auto-renews, no more topping up.
          </p>
          <button type="button" onClick={handleSubscribe} disabled={loading} style={{ width: "100%" }}>
            Subscribe for ₹2499/year
          </button>
        </div>

        {status && (
          <p className="status" style={{ color: status.kind === "error" ? "var(--error)" : "var(--success)" }}>
            {status.message}
          </p>
        )}
      </div>
    </div>
  );
}
