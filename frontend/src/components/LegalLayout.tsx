import { Link } from "react-router-dom";

export const LEGAL_LAST_UPDATED = "11 September 2026";
export const SUPPORT_WHATSAPP = "+91 78277 40390";
export const SUPPORT_WHATSAPP_LINK = "https://wa.me/917827740390";

// Marks text the business still has to fill in before publishing.
export function Placeholder({ children }: { children: string }) {
  return <span className="legal-placeholder">{children}</span>;
}

export default function LegalLayout({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="legal-page">
      <div className="legal-inner">
        <Link to="/">
          <img src="/logo-tight.png?v=3" alt="Skoolgpt" className="login-logo" />
        </Link>
        <h1>{title}</h1>
        <p className="legal-updated">Last updated: {LEGAL_LAST_UPDATED}</p>
        {children}
        <p className="auth-links legal-footer">
          <Link to="/terms">Terms</Link>
          <Link to="/privacy">Privacy</Link>
          <Link to="/refunds">Refunds</Link>
          <Link to="/">Home</Link>
        </p>
      </div>
    </div>
  );
}
