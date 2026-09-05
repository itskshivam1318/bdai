/**
 * Bring-your-own-key settings, held in this browser.
 *
 * The keys never leave `localStorage` except as a header on the request that
 * spends them — see `api/app/byok.py`. Nothing is written to a database and
 * nothing is logged, so clearing site data is the whole uninstall.
 *
 * **Keys are stored per provider, not one at a time.** Switching from Claude to
 * OpenRouter to compare a run is the thing this panel exists for, and a single
 * field would make each switch cost a re-paste — so the previous key is still
 * there when you switch back.
 */

const STORE_KEY = "aivar.settings";

export type Settings = {
  /** A provider id from `GET /api/providers`. Empty means "server decides". */
  provider: string;
  /** provider id → that provider's key. Empty string is the same as absent. */
  keys: Record<string, string>;
  /** Empty means the provider's own cheap default, resolved server-side. */
  model: string;
};

export const EMPTY: Settings = { provider: "", keys: {}, model: "" };

/**
 * One model, as the server describes it. Mirrors `agents/llm/catalog.py` —
 * which is the only place the list is written down, so this type is a shape,
 * not a copy of the contents.
 */
export type ModelChoice = { id: string; label: string; note: string };

export type ProviderSpec = {
  id: string;
  label: string;
  key_env: string;
  key_hint: string;
  default_model: string;
  models: ModelChoice[];
  /** The server's own `.env` already holds a key for this provider. */
  configured: boolean;
};

/**
 * Read what is stored, upgrading the two-field shape this dialog used to have.
 *
 * The migration exists because the old shape is already sitting in the browser
 * of everyone who opened Advanced before today, and silently ignoring it would
 * present an empty form to someone who is sure they pasted a key.
 */
export function loadSettings(): Settings {
  if (typeof window === "undefined") return EMPTY;
  let raw: Record<string, unknown>;
  try {
    raw = JSON.parse(localStorage.getItem(STORE_KEY) ?? "{}");
  } catch {
    return EMPTY;
  }

  if (raw.keys && typeof raw.keys === "object") {
    return {
      provider: typeof raw.provider === "string" ? raw.provider : "",
      keys: raw.keys as Record<string, string>,
      model: typeof raw.model === "string" ? raw.model : "",
    };
  }

  const anthropic = typeof raw.anthropicKey === "string" ? raw.anthropicKey : "";
  const gemini = typeof raw.geminiKey === "string" ? raw.geminiKey : "";
  const model = typeof raw.model === "string" ? raw.model : "";
  return {
    // The old dialog let you hold both keys and pick a model across the two, so
    // the model string is the only record of which one it meant to use.
    provider: model.startsWith("gemini") ? "google" : anthropic ? "claude" : "",
    keys: { claude: anthropic, google: gemini },
    model,
  };
}

export function saveSettings(settings: Settings): void {
  localStorage.setItem(STORE_KEY, JSON.stringify(settings));
}

/** The key for the selected provider, or "" when there is nothing to send. */
export function activeKey(settings: Settings): string {
  return (settings.provider && settings.keys[settings.provider]) || "";
}
