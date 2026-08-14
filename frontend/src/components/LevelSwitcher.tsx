import { useState } from "react";

// Mirrors backend TUTOR_LEVEL_LABELS (app/services/chat_core.py) — kept in
// sync manually since this is static, rarely-changing content not worth a
// network round trip just to fetch 4 constant strings.
const LEVEL_LABELS: Record<number, string> = {
  1: "Level 1 — fastest, lightest on credits",
  2: "Level 2 — quick and well-formatted",
  3: "Level 3 — more detailed explanations",
  4: "Level 4 — most thorough, uses the most credits",
};

interface LevelSwitcherProps {
  level: number | null;
  onChange: (level: number) => Promise<void>;
}

// A persistent header control (see Chat.tsx / MyTutor.tsx) — the web/app
// equivalent of WhatsApp's "🎓 Change Level" menu button and typed "level
// N" command, both of which land on the exact same student.tutor_level
// write server-side. A native <select> rather than a custom dropdown:
// keyboard/screen-reader accessible for free, and a real picker means no
// typing, so no chance of a mistyped level.
export default function LevelSwitcher({ level, onChange }: LevelSwitcherProps) {
  const [saving, setSaving] = useState(false);

  if (level === null) return null;

  async function handleChange(e: React.ChangeEvent<HTMLSelectElement>) {
    const next = Number(e.target.value);
    if (next === level) return;
    setSaving(true);
    try {
      await onChange(next);
    } finally {
      setSaving(false);
    }
  }

  return (
    <label className="level-switcher" title="Switch how fast vs. thorough your tutor's answers are">
      <span className="level-switcher-label">⚙️ Level</span>
      <select value={level} onChange={handleChange} disabled={saving}>
        {Object.entries(LEVEL_LABELS).map(([n, label]) => (
          <option key={n} value={n}>{label}</option>
        ))}
      </select>
    </label>
  );
}
