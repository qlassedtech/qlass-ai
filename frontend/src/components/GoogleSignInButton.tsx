import { useEffect, useRef, useState } from "react";

// Google Identity Services (GIS) has no npm package — it's a script tag
// that attaches `window.google.accounts.id`. Typed loosely here rather
// than pulling in a whole @types package for one small surface.
declare global {
  interface Window {
    google?: {
      accounts: {
        id: {
          initialize: (config: {
            client_id: string;
            callback: (response: { credential: string }) => void;
          }) => void;
          renderButton: (
            parent: HTMLElement,
            options: { type: string; theme: string; size: string; text: string; width?: string },
          ) => void;
        };
      };
    };
  }
}

const SCRIPT_SRC = "https://accounts.google.com/gsi/client";

// Module-level, not component-level — GIS's own script tag is idempotent
// per page (loading it twice is harmless but wasteful), and several of
// these buttons can appear across different pages in one session.
let scriptLoadPromise: Promise<void> | null = null;

function loadGoogleScript(): Promise<void> {
  if (scriptLoadPromise) return scriptLoadPromise;
  scriptLoadPromise = new Promise((resolve, reject) => {
    if (window.google?.accounts?.id) {
      resolve();
      return;
    }
    const script = document.createElement("script");
    script.src = SCRIPT_SRC;
    script.async = true;
    script.defer = true;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error("Couldn't load Google Sign-In"));
    document.head.appendChild(script);
  });
  return scriptLoadPromise;
}

interface GoogleSignInButtonProps {
  onCredential: (idToken: string) => void;
  /** GIS's own button copy — "signin_with" | "signup_with" | "continue_with". */
  text?: "signin_with" | "signup_with" | "continue_with";
  /** Button width in px, as a string (GIS's own API takes it that way). */
  width?: string;
}

/**
 * Renders Google's own "Sign in with Google" button. Silently renders
 * nothing if VITE_GOOGLE_CLIENT_ID isn't configured, rather than showing a
 * broken/non-functional button — Google sign-in is an optional convenience
 * on top of the phone-based flows every account already has, never the
 * only way in.
 */
export default function GoogleSignInButton({ onCredential, text = "signin_with", width = "320" }: GoogleSignInButtonProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [failed, setFailed] = useState(false);
  const clientId = import.meta.env.VITE_GOOGLE_CLIENT_ID as string | undefined;

  useEffect(() => {
    if (!clientId || !containerRef.current) return;
    let cancelled = false;
    loadGoogleScript()
      .then(() => {
        if (cancelled || !containerRef.current || !window.google) return;
        window.google.accounts.id.initialize({
          client_id: clientId,
          callback: (response) => onCredential(response.credential),
        });
        window.google.accounts.id.renderButton(containerRef.current, {
          type: "standard",
          theme: "outline",
          size: "large",
          text,
          width,
        });
      })
      .catch(() => setFailed(true));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clientId, text, width]);

  if (!clientId || failed) return null;
  // Clips Google's own rendered iframe to the app's standard 10px input/
  // button radius — GIS's renderButton API has no border-radius option of
  // its own, and its default corners read as visibly sharper than every
  // other control on the page.
  return <div ref={containerRef} className="google-signin-wrap" />;
}
