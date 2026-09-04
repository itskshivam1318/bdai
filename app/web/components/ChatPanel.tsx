"use client";
import { useEffect, useRef, useState } from "react";
import { api, type ChatMessage, type MapState } from "@/lib/api";

/**
 * The bar at the bottom of the console, and the thread above it.
 *
 * This used to be an input that wrote `Intent: <text>` onto the run timeline
 * and stopped. Nothing read it back and nothing replied, so a control shaped
 * exactly like a chat did not chat -- which is the bug this replaces. Now the
 * states selected on the map ride along as context and a model answers about
 * them.
 *
 * The thread is loaded once and appended to locally. It is not polled: unlike
 * the map and the rail, nothing else writes to it -- a reply only ever arrives
 * as the response to a message sent from here, so a poll would be a request
 * per two seconds that can never find anything.
 *
 * **The draft is owned by `SessionView`, not by this component.** One box does
 * two jobs: Send asks the question, and "Start run" reads the same text as the
 * exploration's intent ("focus on checkout and sign-in"). That was already true
 * of the box this replaces, and it is the only steering input the console has --
 * holding the draft here would have quietly deleted it.
 */
export default function ChatPanel({
  sessionId,
  runId,
  attached,
  onDetach,
  onClearAttached,
  text,
  onTextChange,
}: {
  sessionId: number;
  /** The run whose map the attached states belong to. */
  runId: number | null;
  attached: MapState[];
  onDetach: (key: string) => void;
  onClearAttached: () => void;
  text: string;
  onTextChange: (next: string) => void;
}) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(true);
  const tail = useRef<HTMLDivElement>(null);
  /*
   * Every state attached at any point this session, kept so an older message's
   * chips keep their names after the state is detached. Without it, scrolling
   * back showed `@2c1aeb12` where `@Hall of fame` had been a moment earlier --
   * the row stores keys, and the map is the only place a name lives.
   *
   * Absorbed during render rather than in an effect, the same way `SessionView`
   * clears the selection on a run change: React re-renders immediately with the
   * corrected value, so a chip never paints as a hash for one frame. The
   * condition converges -- after the update nothing is unseen.
   */
  const [known, setKnown] = useState<ReadonlyMap<string, MapState>>(new Map());
  const unseen = attached.filter((s) => !known.has(s.key));
  if (unseen.length) {
    setKnown((current) => {
      const next = new Map(current);
      for (const state of unseen) next.set(state.key, state);
      return next;
    });
  }

  useEffect(() => {
    let cancelled = false;
    api
      .listChat(sessionId)
      .then((thread) => !cancelled && setMessages(thread))
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  // Follow the tail as turns land. `pending` is in the deps so the "thinking"
  // line scrolls into view too -- it is the only feedback during a call that
  // can take ten seconds.
  useEffect(() => {
    if (open) tail.current?.scrollIntoView({ block: "end" });
  }, [messages, pending, open]);

  async function send(e: React.FormEvent) {
    e.preventDefault();
    const question = text.trim();
    if (!question || pending) return;

    // Cleared optimistically, and put back on failure. The API writes the
    // question and the reply together or writes neither, so there is no state
    // where the box is empty and the thread is half-written.
    onTextChange("");
    setError(null);
    setPending(true);
    setOpen(true);
    try {
      const turn = await api.sendChat(
        sessionId,
        question,
        attached.map((s) => s.key),
        runId,
      );
      setMessages((current) => [...current, turn.user, turn.assistant]);
    } catch (err) {
      onTextChange(question);
      setError(err instanceof Error ? err.message : "the message did not send");
    } finally {
      setPending(false);
    }
  }

  async function clear() {
    setMessages([]);
    setError(null);
    await api.clearChat(sessionId).catch(() => {});
  }

  // Built over every state seen, not just the attached ones, so a name is
  // spelled the same way in the chips and in the thread above them. `unseen` is
  // folded in so the very first render of a new chip already has its name.
  const label = nameResolver([...known.values(), ...unseen]);

  return (
    <section className="border-t border-rule">
      {(messages.length > 0 || pending) && (
        <header className="flex items-center gap-2 px-4 pt-2 text-[11px] text-muted">
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            aria-controls="chat-thread"
            className="rounded px-1 hover:bg-hush hover:text-ink"
          >
            {open ? "▾" : "▸"} {messages.length / 2} exchange
            {messages.length === 2 ? "" : "s"}
          </button>
          <button
            type="button"
            onClick={clear}
            className="ml-auto rounded px-1 hover:bg-hush hover:text-ink"
          >
            Clear thread
          </button>
        </header>
      )}

      {open && (messages.length > 0 || pending) && (
        <div
          id="chat-thread"
          className="max-h-[38vh] overflow-y-auto px-4 py-2"
        >
          <ol className="space-y-3">
            {messages.map((message) => (
              <li key={message.id}>
                <Turn message={message} known={known} label={label} />
              </li>
            ))}
          </ol>
          {pending && (
            <p className="mt-3 text-xs text-muted">Reading the map…</p>
          )}
          <div ref={tail} />
        </div>
      )}

      {error && (
        <p role="alert" className="px-4 pb-1 text-xs text-fault">
          {error}
        </p>
      )}

      {attached.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5 px-4 pt-2">
          {attached.map((state) => (
            <button
              key={state.key}
              type="button"
              onClick={() => onDetach(state.key)}
              title="Remove from context"
              className="flex items-center gap-1 rounded-full border border-rule bg-hush px-2 py-0.5 text-[11px] text-ink hover:border-ink"
            >
              <span className="max-w-40 truncate">@{label(state)}</span>
              <span className="text-muted">×</span>
            </button>
          ))}
          <button
            type="button"
            onClick={onClearAttached}
            className="px-1 text-[11px] text-muted hover:text-ink"
          >
            clear
          </button>
        </div>
      )}

      <form onSubmit={send} className="flex items-center gap-2 px-4 py-3">
        <label htmlFor="chat" className="sr-only">
          Ask about the map
        </label>
        <input
          id="chat"
          value={text}
          onChange={(e) => onTextChange(e.target.value)}
          disabled={pending}
          placeholder={
            attached.length
              ? `Ask about ${attached.length} attached state${attached.length === 1 ? "" : "s"}…`
              : "Click a state on the map to attach it, or ask about the map as a whole"
          }
          className="min-w-0 flex-1 rounded-md border border-rule bg-paper px-3 py-2 text-sm outline-none focus:border-ink disabled:opacity-50"
        />
        <button
          type="submit"
          disabled={!text.trim() || pending}
          className="rounded-md border border-rule px-3 py-2 text-sm disabled:opacity-30"
        >
          {pending ? "…" : "Send"}
        </button>
      </form>
    </section>
  );
}

/**
 * One turn. The chips under a question show what was attached *when it was
 * asked* -- `node_keys` is stored on the row for exactly this reason, so
 * scrolling back explains an answer that no longer matches the selection.
 */
function Turn({
  message,
  known,
  label,
}: {
  message: ChatMessage;
  /** Every state attached this session, by key. See `known` in ChatPanel. */
  known: ReadonlyMap<string, MapState>;
  label: (state: MapState) => string;
}) {
  let keys: string[] = [];
  try {
    keys = JSON.parse(message.node_keys) as string[];
  } catch {
    keys = [];
  }

  if (message.role === "user") {
    return (
      <div className="ml-auto max-w-[85%] rounded-md bg-hush px-3 py-2">
        <p className="whitespace-pre-wrap break-words text-sm text-ink">
          {message.content}
        </p>
        {keys.length > 0 && (
          <p className="mt-1 truncate text-[11px] text-muted">
            {keys
              .map((k) => {
                const state = known.get(k);
                return `@${state ? label(state) : k.slice(0, 8)}`;
              })
              .join("  ")}
          </p>
        )}
      </div>
    );
  }

  return (
    <div className="max-w-[85%] space-y-1.5">
      {message.content.split("\n").map((line, i) => (
        <Line key={i} text={line} />
      ))}
    </div>
  );
}

/**
 * The three marks a model reaches for unprompted, and nothing else.
 *
 * A markdown library would be a dependency and a bundle for a panel that shows
 * a few short paragraphs. Not rendering them at all is the worse option: the
 * first live answer came back with `**passed**` and `` `hall_of_fame.php` ``
 * in it, and printing the asterisks is a visible defect in the demo. The
 * prompt asks for exactly this subset -- see `prompts/analyst.md` -- so the two
 * halves are one contract.
 */
function Line({ text }: { text: string }) {
  const bullet = /^\s*[-*]\s+/.test(text);
  const body = bullet ? text.replace(/^\s*[-*]\s+/, "") : text;

  if (!body.trim()) return null;

  // Split on the delimiters while keeping them, so the parts alternate between
  // plain text and marked-up spans without a parser.
  // `**` before `*`, or bold gets eaten as two empty italics.
  const parts = body
    .split(/(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)/g)
    .filter(Boolean);
  const rendered = parts.map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**")) {
      return (
        <strong key={i} className="font-medium">
          {part.slice(2, -2)}
        </strong>
      );
    }
    if (part.startsWith("*") && part.endsWith("*") && part.length > 2) {
      return <em key={i}>{part.slice(1, -1)}</em>;
    }
    if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
      return (
        <code key={i} className="rounded bg-hush px-1 font-mono text-[12px]">
          {part.slice(1, -1)}
        </code>
      );
    }
    return <span key={i}>{part}</span>;
  });

  return (
    <p
      className={`break-words text-sm leading-relaxed text-ink ${
        bullet ? "pl-4 -indent-2" : ""
      }`}
    >
      {bullet && <span className="text-muted">— </span>}
      {rendered}
    </p>
  );
}

/**
 * Names a state the way the map does, and disambiguates when that is not
 * enough.
 *
 * Every state on our own SUT is titled "Testing Challenges", so attaching two
 * of them produced two chips reading `@Testing Challenges ×` with nothing to
 * tell them apart — the selection was visible and unreadable at the same time.
 * A key prefix is added only to the names that actually collide, so the common
 * case stays clean.
 *
 * Fed every state seen this session rather than the currently attached ones:
 * a name that gains a suffix when a second state is attached, and loses it
 * again when that state is detached, is a name that moves while you read it.
 */
function nameResolver(states: MapState[]) {
  const plain = (s: MapState) => s.label ?? s.title ?? s.url;
  const seen = new Map<string, number>();
  for (const state of states) {
    seen.set(plain(state), (seen.get(plain(state)) ?? 0) + 1);
  }
  return (state: MapState) => {
    const name = plain(state);
    return (seen.get(name) ?? 0) > 1
      ? `${name} ${state.key.slice(0, 6)}`
      : name;
  };
}
