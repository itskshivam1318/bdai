"use client";
import { useEffect, useRef, useState } from "react";

/**
 * Keys are held in localStorage, never sent to our API — the backend reads its
 * own from `.env`. This dialog exists so a demo machine can switch provider
 * without a restart; it is not a secret store.
 */
const STORE_KEY = "aivar.settings";

export type Settings = {
  anthropicKey: string;
  geminiKey: string;
  model: string;
};

const EMPTY: Settings = {
  anthropicKey: "",
  geminiKey: "",
  model: "claude-sonnet-5",
};

const MODELS = [
  { id: "claude-opus-5", label: "Claude Opus 5" },
  { id: "claude-sonnet-5", label: "Claude Sonnet 5" },
  { id: "claude-haiku-4-5-20251001", label: "Claude Haiku 4.5" },
  { id: "gemini-2.5-pro", label: "Gemini 2.5 Pro" },
];

export function loadSettings(): Settings {
  if (typeof window === "undefined") return EMPTY;
  try {
    return { ...EMPTY, ...JSON.parse(localStorage.getItem(STORE_KEY) ?? "{}") };
  } catch {
    return EMPTY;
  }
}

function KeyField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
}) {
  const [shown, setShown] = useState(false);
  return (
    <div>
      <label className="block text-sm">{label}</label>
      <div className="mt-1 flex gap-2">
        <input
          type={shown ? "text" : "password"}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="sk-…"
          spellCheck={false}
          className="min-w-0 flex-1 rounded-md border border-rule bg-paper px-2 py-1.5 font-mono text-sm outline-none focus:border-ink"
        />
        <button
          type="button"
          onClick={() => setShown((v) => !v)}
          className="rounded-md border border-rule px-2 text-xs text-muted hover:bg-hush"
        >
          {shown ? "Hide" : "Show"}
        </button>
      </div>
    </div>
  );
}

export default function SettingsDialog({ onClose }: { onClose: () => void }) {
  // Lazy initialiser, not an effect: the dialog only ever mounts on the client
  // (it renders behind a click), so localStorage is readable on first render.
  const [draft, setDraft] = useState<Settings>(loadSettings);
  const firstField = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    firstField.current?.querySelector("input")?.focus();
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  function save() {
    localStorage.setItem(STORE_KEY, JSON.stringify(draft));
    onClose();
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-ink/30 p-4"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="settings-title"
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-md rounded-lg border border-rule bg-paper p-6 shadow-lg"
      >
        <h2 id="settings-title" className="text-base font-medium">
          Advanced
        </h2>
        <p className="mt-1 text-sm text-muted">
          Kept in this browser. The agent falls back to the server&rsquo;s own keys.
        </p>

        <div ref={firstField} className="mt-5 space-y-4">
          <KeyField
            label="Claude API key"
            value={draft.anthropicKey}
            onChange={(anthropicKey) => setDraft({ ...draft, anthropicKey })}
          />
          <KeyField
            label="Gemini API key"
            value={draft.geminiKey}
            onChange={(geminiKey) => setDraft({ ...draft, geminiKey })}
          />
          <div>
            <label htmlFor="model" className="block text-sm">
              Model
            </label>
            <select
              id="model"
              value={draft.model}
              onChange={(e) => setDraft({ ...draft, model: e.target.value })}
              className="mt-1 w-full rounded-md border border-rule bg-paper px-2 py-1.5 text-sm outline-none focus:border-ink"
            >
              {MODELS.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.label}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="mt-6 flex justify-end gap-2">
          <button
            onClick={onClose}
            className="rounded-md border border-rule px-3 py-1.5 text-sm hover:bg-hush"
          >
            Cancel
          </button>
          <button
            onClick={save}
            className="rounded-md bg-ink px-3 py-1.5 text-sm text-paper"
          >
            Save
          </button>
        </div>
      </div>
    </div>
  );
}
