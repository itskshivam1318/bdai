"use client";
import { useEffect, useRef, useState } from "react";
import { api, type ChatMessage, type ChatThread, type MapState } from "@/lib/api";

/**
 * One chat window: its own conversation, its own selection, its own draft.
 *
 * This was a single bar across the bottom of the console holding one thread and
 * one draft shared with the run's intent box. Two things forced it apart. A
 * person asking "why did sign-in split" and "what has no coverage" is running
 * two investigations, and a single transcript makes each the other's noise --
 * for the reader and for the model, which now genuinely carries the thread as
 * turns rather than as prose. And a selection belongs to a question: with one
 * global set of attached states, opening a second conversation meant destroying
 * the first one's context.
 *
 * The thread is loaded once and appended to locally. It is not polled: unlike
 * the map and the rail, nothing else writes to it -- a reply only ever arrives
 * as the response to a message sent from here, so a poll would be a request per
 * two seconds that can never find anything.
 *
 * The draft *is* held here now, and that is the change worth naming. It used to
 * live in `SessionView` because Send and "Start run" read the same box. Two
 * windows cannot share one draft without one of them silently typing into the
 * other, so the intent box went back to being an intent box and each window
 * owns its text.
 */
export default function ChatWindow({
  thread,
  runId,
  attached,
  focused,
  onFocus,
  onDetach,
  onClearAttached,
  onPatch,
}: {
  thread: ChatThread;
  /** The run whose map the attached states belong to. */
  runId: number | null;
  attached: MapState[];
  /** Clicks on the map attach to the focused window, and only that one. */
  focused: boolean;
  onFocus: () => void;
  onDetach: (key: string) => void;
  onClearAttached: () => void;
  onPatch: (patch: { title?: string; open?: boolean; minimised?: boolean }) => void;
}) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [text, setText] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [renaming, setRenaming] = useState(false);
  const [draftTitle, setDraftTitle] = useState(thread.title);
  const tail = useRef<HTMLDivElement>(null);
  /*
   * Every state attached at any point in this window, kept so an older
   * message's chips keep their names after the state is detached. Without it,
   * scrolling back showed `@2c1aeb12` where `@Hall of fame` had been a moment
   * earlier -- the row stores keys, and the map is the only place a name lives.
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
      .listChat(thread.id)
      .then((rows) => !cancelled && setMessages(rows))
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [thread.id]);

  // Follow the tail as turns land. `pending` is in the deps so the "thinking"
  // line scrolls into view too -- it is the only feedback during a call that
  // can take ten seconds.
  useEffect(() => {
    if (!thread.minimised) tail.current?.scrollIntoView({ block: "end" });
  }, [messages, pending, thread.minimised]);

  async function send(e: React.FormEvent) {
    e.preventDefault();
    const question = text.trim();
    if (!question || pending) return;

    // Cleared optimistically, and put back on failure. The API writes the
    // question and the reply together or writes neither, so there is no state
    // where the box is empty and the thread is half-written.
    setText("");
    setError(null);
    setPending(true);
    try {
      const turn = await api.sendChat(
        thread.id,
        question,
        attached.map((s) => s.key),
        runId,
      );
      setMessages((current) => [...current, turn.user, turn.assistant]);
      // The first question names the window. The server decides that -- it
      // knows whether the thread was empty and whether anyone has renamed it --
      // and sends the thread back so the title bar does not need a second
      // request to find out.
      if (turn.thread.title !== thread.title) onPatch({ title: turn.thread.title });
    } catch (err) {
      setText(question);
      setError(err instanceof Error ? err.message : "the message did not send");
    } finally {
      setPending(false);
    }
  }

  async function clear() {
    setMessages([]);
    setError(null);
    await api.clearChat(thread.id).catch(() => {});
  }

  function commitTitle() {
    setRenaming(false);
    const next = draftTitle.trim();
    if (!next || next === thread.title) {
      setDraftTitle(thread.title);
      return;
    }
    onPatch({ title: next });
  }

  // Built over every state seen, not just the attached ones, so a name is
  // spelled the same way in the chips and in the thread above them. `unseen` is
  // folded in so the very first render of a new chip already has its name.
  const label = nameResolver([...known.values(), ...unseen]);

  /*
   * A focused window is outlined, not tinted. The map's rings and the verdict
   * tones are already carrying meaning about the application; a second colour
   * over here would compete with them for the same glance. The outline says
   * only "clicks on the map land in this one", which is exactly what it means.
   */
  const frame = `pointer-events-auto flex flex-col rounded-lg border bg-paper shadow-lg ${
    focused ? "border-ink" : "border-rule"
  }`;

  return (
    <section
      className={frame}
      onMouseDownCapture={onFocus}
      onFocusCapture={onFocus}
      aria-label={`Chat: ${thread.title}`}
    >
      <header className="flex items-center gap-1 border-b border-rule px-2 py-1.5">
        <button
          type="button"
          onClick={() => onPatch({ minimised: !thread.minimised })}
          aria-expanded={!thread.minimised}
          title={thread.minimised ? "Expand" : "Minimise"}
          // Bigger than the other header glyphs on purpose: at 11px the
          // triangles render as a smudge in Geist, and this is the control the
          // whole "put it away without losing it" gesture hangs on.
          className="rounded px-1 text-[13px] leading-none text-muted hover:bg-hush hover:text-ink"
        >
          {thread.minimised ? "▸" : "▾"}
        </button>

        {renaming ? (
          <input
            autoFocus
            value={draftTitle}
            onChange={(e) => setDraftTitle(e.target.value)}
            onBlur={commitTitle}
            onKeyDown={(e) => {
              if (e.key === "Enter") e.currentTarget.blur();
              if (e.key === "Escape") {
                setDraftTitle(thread.title);
                setRenaming(false);
              }
            }}
            aria-label="Chat name"
            className="min-w-0 flex-1 rounded bg-hush px-1 py-0.5 text-xs outline-none"
          />
        ) : (
          <button
            type="button"
            onDoubleClick={() => {
              setDraftTitle(thread.title);
              setRenaming(true);
            }}
            onClick={() => thread.minimised && onPatch({ minimised: false })}
            title={`${thread.title} — double-click to rename`}
            className="min-w-0 flex-1 truncate rounded px-1 py-0.5 text-left text-xs font-medium hover:bg-hush"
          >
            {thread.title}
          </button>
        )}

        {messages.length > 0 && (
          <span className="shrink-0 text-[10px] text-muted">
            {messages.length / 2}
          </span>
        )}
        {/* Clear empties the conversation; close puts the window away and
            keeps it. Only the ⨯ in the reopen list destroys anything, and it
            asks first — three controls that all look like "make this go away"
            need the destructive one to be the hardest to reach. */}
        <button
          type="button"
          onClick={clear}
          disabled={!messages.length}
          title="Clear this conversation"
          className="shrink-0 rounded px-1 text-[11px] text-muted hover:bg-hush hover:text-ink disabled:opacity-30"
        >
          ⌫
        </button>
        <button
          type="button"
          onClick={() => onPatch({ open: false })}
          title="Close — the conversation is kept"
          className="shrink-0 rounded px-1 text-[11px] text-muted hover:bg-hush hover:text-ink"
        >
          ✕
        </button>
      </header>

      {!thread.minimised && (
        <>
          <div className="max-h-[42vh] min-h-24 flex-1 overflow-y-auto px-3 py-2">
            {messages.length === 0 && !pending && (
              <p className="py-4 text-center text-[11px] text-muted">
                Click states on the map to attach them, then ask.
              </p>
            )}
            <ol className="space-y-3">
              {messages.map((message) => (
                <li key={message.id}>
                  <Turn message={message} known={known} label={label} />
                </li>
              ))}
            </ol>
            {pending && <p className="mt-3 text-xs text-muted">Reading the map…</p>}
            <div ref={tail} />
          </div>

          {error && (
            <p role="alert" className="px-3 pb-1 text-xs text-fault">
              {error}
            </p>
          )}

          {attached.length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5 border-t border-rule px-3 pt-2">
              {attached.map((state) => (
                <button
                  key={state.key}
                  type="button"
                  onClick={() => onDetach(state.key)}
                  title="Remove from context"
                  className="flex items-center gap-1 rounded-full border border-rule bg-hush px-2 py-0.5 text-[11px] text-ink hover:border-ink"
                >
                  <span className="max-w-32 truncate">@{label(state)}</span>
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

          <form onSubmit={send} className="flex items-center gap-2 px-3 py-2">
            <label htmlFor={`chat-${thread.id}`} className="sr-only">
              Ask about the map
            </label>
            <input
              id={`chat-${thread.id}`}
              value={text}
              onChange={(e) => setText(e.target.value)}
              disabled={pending}
              placeholder={
                attached.length
                  ? `Ask about ${attached.length} state${attached.length === 1 ? "" : "s"}…`
                  : focused
                    ? "Ask about the map…"
                    : "Click here to aim the map at this chat"
              }
              className="min-w-0 flex-1 rounded-md border border-rule bg-paper px-2.5 py-1.5 text-sm outline-none focus:border-ink disabled:opacity-50"
            />
            <button
              type="submit"
              disabled={!text.trim() || pending}
              className="rounded-md border border-rule px-2.5 py-1.5 text-xs disabled:opacity-30"
            >
              {pending ? "…" : "Send"}
            </button>
          </form>
        </>
      )}
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
  /** Every state attached in this window, by key. See `known` in ChatWindow. */
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
      <div className="ml-auto max-w-[90%] rounded-md bg-hush px-2.5 py-1.5">
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
    <div className="max-w-[95%] space-y-1.5">
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
 * Fed every state seen in this window rather than the currently attached ones:
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
    return (seen.get(name) ?? 0) > 1 ? `${name} ${state.key.slice(0, 6)}` : name;
  };
}
