"use client";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { api, type TestSession } from "@/lib/api";

/** Typing "shop.example" means https://shop.example, not a relative path. */
function normalise(raw: string): string {
  const trimmed = raw.trim();
  if (!trimmed) return "";
  return /^https?:\/\//i.test(trimmed) ? trimmed : `https://${trimmed}`;
}

export default function NewSession() {
  const router = useRouter();
  const [url, setUrl] = useState("");
  const [context, setContext] = useState("");
  // Closed by default, and that is the claim being made: a URL is the whole
  // required input. A second box sitting open beside the first would say the
  // opposite before anyone had read a word.
  const [showContext, setShowContext] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  /*
   * A session created by a Start that then failed to launch its run.
   *
   * Kept so that pressing Start again starts the run on *that* session rather
   * than leaving a second, empty one in the sidebar every time the API blinks.
   * Reused only while the typed URL and context still match what was sent --
   * edit either and it is a different session being asked for.
   */
  const started = useRef<TestSession | null>(null);

  async function start(e: React.FormEvent) {
    e.preventDefault();
    const target = normalise(url);
    if (!target) return;
    setBusy(true);
    setError(null);
    try {
      const note = context.trim();
      const kept = started.current;
      const session =
        kept && kept.target_url === target && (kept.context ?? "") === note
          ? kept
          : await api.createSession(target, note);
      started.current = session;
      /*
       * Start starts it. The session used to be all this button made, and the
       * run waited on a second click on "Start run" in the console -- so the
       * one input the product claims to need was a form that submitted to
       * another form. Creating the run here is what makes the claim true.
       */
      const run = await api.createRun(session.target_url, session.id);
      // Explicitly not awaited: the colony runs for minutes and this button
      // has a page to go to. Progress arrives on the session's timeline.
      void api.explore(run.id).catch(() => {});
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

          {/*
            One box, not three. Nobody thinks "credentials, focus, claims" —
            they think of what they would tell a colleague handing over the app,
            and a model sorts that into fields at run time. See
            `api/agents/context.py`.
          */}
          {showContext ? (
            <div className="mt-5">
              <label
                htmlFor="context"
                className="text-xs uppercase tracking-wide text-muted"
              >
                Anything else we should know
              </label>
              <textarea
                id="context"
                value={context}
                onChange={(e) => setContext(e.target.value)}
                rows={4}
                autoFocus
                disabled={busy}
                placeholder={
                  "Log in as standard_user / secret_sauce.\n" +
                  "Focus on checkout.\n" +
                  "Check that an out-of-stock item can't be added to the cart."
                }
                className="mt-2 w-full resize-y rounded-md border border-rule bg-paper px-3 py-2 text-sm leading-relaxed outline-none focus:border-ink placeholder:text-muted/60"
              />
              <p className="mt-2 text-xs leading-relaxed text-muted">
                Credentials are typed into the app under test and stored as
                written — treat this like any other test account.
              </p>
            </div>
          ) : (
            <button
              type="button"
              onClick={() => setShowContext(true)}
              className="mt-4 text-sm text-muted underline decoration-rule underline-offset-4 hover:text-ink"
            >
              Add context — a login, what to focus on, something to check
            </button>
          )}
        </form>

        {error && <p className="mt-3 text-sm text-fault">{error}</p>}
      </div>
    </div>
  );
}
