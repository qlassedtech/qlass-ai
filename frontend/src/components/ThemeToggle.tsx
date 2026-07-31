import { useState } from "react";
import { getEffectiveTheme, toggleTheme } from "../theme";

export default function ThemeToggle() {
  const [mode, setMode] = useState(getEffectiveTheme);

  return (
    <button
      type="button"
      className="theme-toggle"
      onClick={() => setMode(toggleTheme())}
      aria-label={mode === "dark" ? "Switch to light mode" : "Switch to dark mode"}
      title={mode === "dark" ? "Switch to light mode" : "Switch to dark mode"}
    >
      {mode === "dark" ? "☀️" : "🌙"}
    </button>
  );
}
