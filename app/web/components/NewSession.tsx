"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { api } from "@/lib/api";

/** Typing "shop.example" means https://shop.example, not a relative path. */
function normalise(raw: string): string {
  const trimmed = raw.trim();
  if (!trimmed) return "";
  return /^https?:\/\//i.test(trimmed) ? trimmed : `https://${trimmed}`;
}

export default function NewSession() {
  const router = useRouter();
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function start(e: React.FormEvent) {
    e.preventDefault();
    const target = normalise(url);
    if (!target) return;
    setBusy(true);
    setError(null);
    try {
      const session = await api.createSession(target);
      router.push(`/s/${session.id}`);
    } catch {
      setError("Couldn't reach the API. Is it running on port 8000?");
      setBusy(false);
    }
  }

  return (
    // Optically centred rather than mathematically: the eye reads a block
    // sitting slightly above centre as centred.
    <div className="flex flex-1 flex-col justify-center px-8 pb-24">
      <div className="mx-auto w-full max-w-xl">
        <h1 className="text-2xl font-medium tracking-tight">
          What do you want to test?
        </h1>
        <p className="mt-2 max-w-md text-sm leading-relaxed text-muted">
          A URL is all it needs. The agent explores the app, writes a plan, checks
          it for gaps, generates the suite and runs it.
        </p>

        <form onSubmit={start} className="mt-8">
          {/* A ruled line, not a box: the URL is the whole input, so it reads
              as something you write on rather than something you fill in. */}
          <div className="flex items-baseline gap-3 border-b-2 border-rule pb-2 focus-within:border-ink">
            <label htmlFor="target" className="sr-only">
              Application URL
            </label>
            <input
              id="target"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://"
              autoFocus
              autoComplete="url"
              spellCheck={false}
              disabled={busy}
              // The rule below thickens on focus, so a second outline around
              // the field would just be two focus indicators arguing.
              className="min-w-0 flex-1 bg-transparent font-mono text-lg outline-none focus-visible:outline-none placeholder:text-muted/60"
            />
            <button
              type="submit"
              disabled={!url.trim() || busy}
              className="shrink-0 rounded-md bg-ink px-3 py-1.5 text-sm text-paper disabled:opacity-30"
            >
              {busy ? "Starting" : "Start"}
            </button>
          </div>
        </form>

        {error && <p className="mt-3 text-sm text-fault">{error}</p>}
      </div>
    </div>
  );
}
