"use client";
import { useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";
import {
  activeKey,
  loadSettings,
  saveSettings,
  type ProviderSpec,
  type Settings,
} from "@/lib/settings";

/**
 * Bring your own key: pick a provider, paste its key, choose a model.
 *
 * **The list comes from the server**, not from a constant here — `GET
 * /api/providers` serves `agents/llm/catalog.py`, which is also what `load()`
 * resolves against. A model offered here is therefore a model the backend can
 * construct, which a hand-kept array in this file could not promise.
 *
 * Keys go to `localStorage` and travel as request headers; the server holds
 * none of them. See `lib/settings.ts` and `api/app/byok.py`.
 */

/** The escape hatch's sentinel. OpenRouter alone routes hundreds of models. */
const CUSTOM = "__custom__";

function KeyField({
  spec,
  value,
  onChange,
}: {
  spec: ProviderSpec;
  value: string;
  onChange: (v: string) => void;
}) {
  const [shown, setShown] = useState(false);
  return (
    <div>
      <label htmlFor="byok-key" className="block text-sm">
        {spec.label} API key
      </label>
      <div className="mt-1 flex gap-2">
        <input
          id="byok-key"
          type={shown ? "text" : "password"}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={spec.key_hint}
          spellCheck={false}
          autoComplete="off"
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
      <p className="mt-1 text-xs text-muted">
        {value
          ? `Sent with each run. Never stored on the server.`
          : spec.configured
            ? `Leave empty to use the server's ${spec.key_env}.`
            : `The server has no ${spec.key_env}, so a run needs this.`}
      </p>
    </div>
  );
}

export default function SettingsDialog({ onClose }: { onClose: () => void }) {
  // Lazy initialiser, not an effect: the dialog only ever mounts on the client
  // (it renders behind a click), so localStorage is readable on first render.
  const [draft, setDraft] = useState<Settings>(loadSettings);
  const [providers, setProviders] = useState<ProviderSpec[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Whether Custom is *open*, which is not the same question as whether the
  // current model is unlisted. Choosing Custom seeds the box with the model
  // already selected — the nearest starting point for the id being typed — and
  // that seed is a listed id, so a purely derived flag would close the box the
  // instant it opened. Held separately, and cleared whenever a listed model or
  // a different provider is chosen.
  const [customOpen, setCustomOpen] = useState(false);
  const firstField = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    firstField.current?.querySelector("select")?.focus();
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    let live = true;
    api
      .listProviders()
      .then((rows) => live && setProviders(rows))
      // The dialog is useless without the catalogue, and an empty select that
      // looks like "no providers exist" is worse than saying the API is down.
      .catch((e: Error) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, []);

  const spec = providers?.find((p) => p.id === draft.provider) ?? null;
  const known = spec?.models.some((m) => m.id === draft.model) ?? false;
  // Open because it was asked for, or because what is stored is an id this
  // provider does not list — a model typed here on an earlier visit has to come
  // back in the box it was typed into. Empty means "the provider's default",
  // which is a listed row, not a custom one.
  const custom =
    spec !== null && (customOpen || (draft.model !== "" && !known));

  /**
   * Switching provider re-defaults the model, because a model id belongs to
   * exactly one provider: carrying `claude-opus-5` across to OpenRouter would
   * produce a run that fails at the first call with a 404 from a vendor the
   * user never chose. The key is kept per provider, so switching back restores
   * it — see `lib/settings.ts`.
   */
  function pickProvider(id: string) {
    const next = providers?.find((p) => p.id === id);
    setCustomOpen(false);
    setDraft({ ...draft, provider: id, model: next?.default_model ?? "" });
  }

  function pickModel(value: string) {
    if (value === CUSTOM) {
      setCustomOpen(true);
      // Seeded with what is selected now rather than blanked: it is the nearest
      // starting point for the id being typed, and an empty required box is a
      // worse place to land.
      setDraft({ ...draft, model: draft.model || (spec?.default_model ?? "") });
      return;
    }
    setCustomOpen(false);
    setDraft({ ...draft, model: value });
  }

  function save() {
    saveSettings(draft);
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
          Bring your own key. Kept in this browser and sent with each run — the
          server stores nothing. Leave the provider unset and the agent uses the
          server&rsquo;s own keys.
        </p>

        {error && (
          <p className="mt-4 rounded-md border border-rule bg-hush px-3 py-2 text-sm">
            Could not load providers: {error}
          </p>
        )}

        <div ref={firstField} className="mt-5 space-y-4">
          <div>
            <label htmlFor="provider" className="block text-sm">
              Provider
            </label>
            <select
              id="provider"
              value={draft.provider}
              disabled={!providers}
              onChange={(e) => pickProvider(e.target.value)}
              className="mt-1 w-full rounded-md border border-rule bg-paper px-2 py-1.5 text-sm outline-none focus:border-ink disabled:opacity-50"
            >
              <option value="">
                {providers ? "Server default" : "Loading…"}
              </option>
              {(providers ?? []).map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label}
                  {p.configured ? " — server key set" : ""}
                </option>
              ))}
            </select>
          </div>

          {spec && (
            <>
              <KeyField
                spec={spec}
                value={activeKey(draft)}
                onChange={(key) =>
                  setDraft({
                    ...draft,
                    keys: { ...draft.keys, [spec.id]: key },
                  })
                }
              />

              <div>
                <label htmlFor="model" className="block text-sm">
                  Model
                </label>
                <select
                  id="model"
                  value={custom ? CUSTOM : draft.model}
                  onChange={(e) => pickModel(e.target.value)}
                  className="mt-1 w-full rounded-md border border-rule bg-paper px-2 py-1.5 text-sm outline-none focus:border-ink"
                >
                  {spec.models.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.label}
                      {m.id === spec.default_model ? " (default)" : ""}
                      {m.note ? ` — ${m.note}` : ""}
                    </option>
                  ))}
                  <option value={CUSTOM}>Custom…</option>
                </select>
                {custom && (
                  <input
                    aria-label="Custom model id"
                    value={draft.model}
                    onChange={(e) =>
                      setDraft({ ...draft, model: e.target.value })
                    }
                    placeholder={spec.default_model}
                    spellCheck={false}
                    className="mt-2 w-full rounded-md border border-rule bg-paper px-2 py-1.5 font-mono text-sm outline-none focus:border-ink"
                  />
                )}
              </div>
            </>
          )}
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
