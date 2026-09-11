import { Link } from "react-router-dom";

export default function LegalFooter() {
  return (
    <p className="auth-links legal-footer">
      <Link to="/terms">Terms</Link>
      <Link to="/privacy">Privacy</Link>
      <Link to="/refunds">Refunds</Link>
    </p>
  );
}

export function ConsentLine() {
  return (
    <p className="consent-line">
      By continuing you agree to the <Link to="/terms">Terms</Link> and <Link to="/privacy">Privacy Policy</Link>.
    </p>
  );
}
