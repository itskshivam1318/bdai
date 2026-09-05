"""Chat windows beside the map: threads, and a real multi-turn conversation.

    GET    /api/sessions/{id}/chat/threads      list the windows
    POST   /api/sessions/{id}/chat/threads      open a new one
    GET    /api/chat/threads/{tid}/messages     one thread's history
    POST   /api/chat/threads/{tid}/messages     ask, and answer
    PATCH  /api/chat/threads/{tid}              rename, close, minimise
    DELETE /api/chat/threads/{tid}              destroy it and its messages

**Why threads.** One question is rarely one subject. "Why did sign-in split in
two" and "what has no coverage" are separate investigations, and running them
down a single transcript makes each the other's noise. A window per subject is
also the only way to keep two different *selections* alive at once -- each
thread carries its own attached states.

**Why this is a real conversation now.** It used to render the whole thread into
one user message and send a single stateless completion: the model saw
`Them: ... You: ...` as prose inside its own opening prompt, not as turns it had
taken. `Exchange.follow_up` in `agents/llm` closed that -- an ant's round ends
with tool results, a chat's round ends with a follow-up question, and both are
"the user turn that answers the model". So the transcript now serialises to
genuinely alternating messages, on all three providers.

**What each turn carries.** The map index and the *full* detail of the attached
states ride on the **current** question only; older questions keep just the
names of what was attached to them. This is how a chat with attachments actually
behaves -- you do not re-send yesterday's document -- and it is why the thread
got cheaper rather than more expensive when it became multi-turn. What the model
knew about an old state is already in its own reply, which is in the transcript.

**Why the context is assembled here rather than by a tool call.** The model gets
no tools. Everything it could ask for is small enough to hand over up front, and
a read-only question does not need a loop that could decide to crawl something.
`agents/orchestrator.py` is where tool-calling belongs; this is a reader.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from agents.ant import instructions
from agents.llm import Exchange, Transcript, load
from agents.suite import verdicts_by_state
from agents.tracing import save_transcript

from ..db import get_session
from ..models import AppState, ChatMessage, ChatThread, Run, StateTransition, TestSession

router = APIRouter(tags=["chat"])

# How many past messages are replayed. Twenty is roughly ten exchanges -- past
# that a question is almost always about something recent. Lower than it could
# be on purpose: every turn also re-sends the map, and the map is the big part.
HISTORY_LIMIT = 20

# A window's name is the first thing asked in it, truncated. Long enough to tell
# two investigations apart in a title bar, short enough not to wrap.
TITLE_CHARS = 42


class ThreadCreate(BaseModel):
    title: str | None = None


class ThreadPatch(BaseModel):
    """Every field optional: this is three different edits through one route.

    `open` and `minimised` are window state and are written on every collapse
    and close, so they must not require the client to know the title.
    """

    title: str | None = None
    open: bool | None = None
    minimised: bool | None = None


class ChatRequest(BaseModel):
    text: str
    # `AppState.key` values the user had selected on the map. Empty is allowed:
    # asking about the map as a whole is a real question.
    node_keys: list[str] = []
    # Which run's map those keys belong to. Without it the keys are ambiguous
    # across re-crawls; with no run at all there is simply no map to attach.
    run_id: int | None = None


class ChatTurn(BaseModel):
    """One message plus the reply it produced.

    Both are returned because both are written in one transaction -- see
    `send()` for why the user's message is not persisted before the model
    answers. `thread` comes back too because the first message of a thread
    renames it, and the title bar should not need a second request to find out.
    """

    user: ChatMessage
    assistant: ChatMessage
    thread: ChatThread


# --------------------------------------------------------------------------
# Rendering the map into a prompt
# --------------------------------------------------------------------------


def _loads(raw: str | None, fallback):
    try:
        return json.loads(raw or "")
    except (json.JSONDecodeError, TypeError):
        return fallback


def _state_line(row: AppState, verdict: str | None) -> str:
    """One state, one line -- the map as an index."""
    name = row.label or row.title or row.url
    fields = _loads(row.fields, [])
    actions = _loads(row.actions, [])
    marks = [f"{len(fields)} field(s)", f"{len(actions)} action(s)"]
    if row.is_entry:
        marks.insert(0, "entry")
    marks.append(verdict or "untested")
    return f"- [{row.key[:8]}] {name} -- {row.url} ({', '.join(marks)})"


def _attached_block(
    row: AppState, verdict: str | None, edges: list[StateTransition], by_key: dict
) -> str:
    """One attached state, in full. This is what the question is about."""
    name = row.label or row.title or row.url
    lines = [
        f"### {name}  [{row.key[:8]}]",
        f"url      {row.url}",
        f"title    {row.title}",
        f"verdict  {verdict or 'untested -- no scenario has crossed this state'}",
    ]
    # Attribution, when the map has it. Two states found by the same ant on the
    # same wave were reached the same way, which is often the answer to "why do
    # these two look identical".
    if row.found_by:
        lines.append(f"found by {row.found_by}")

    fields = _loads(row.fields, [])
    if fields:
        lines.append("fields   " + ", ".join(f"{r}:{n}" for r, n in fields))

    actions = _loads(row.actions, [])
    if actions:
        lines.append("actions")
        lines += [f"  - {a}" for a in actions]

    leaving = [e for e in edges if e.from_key == row.key]
    if leaving:
        lines.append("edges leaving")
        for edge in leaving:
            target = by_key.get(edge.to_key)
            dest = (
                "back to itself"
                if edge.to_key == row.key
                else (target.label or target.title if target else edge.to_key[:8])
            )
            mark = " [mutating]" if edge.mutating else ""
            lines.append(f"  - {edge.action} -> {dest}{mark}")

    # Named as edges, not as visits. The first live answer read "reached by 2
    # actions" as "two scenarios passed through here" -- these are transitions
    # the crawl recorded, and say nothing about how often anything ran.
    arriving = [e for e in edges if e.to_key == row.key and e.from_key != row.key]
    if arriving:
        lines.append("edges arriving")
        for edge in arriving:
            source = by_key.get(edge.from_key)
            origin = source.label or source.title if source else edge.from_key[:8]
            lines.append(f"  - {edge.action} from {origin}")

    return "\n".join(lines)


def _name_of(key: str, by_key: dict) -> str:
    row = by_key.get(key)
    return (row.label or row.title or row.url) if row else key[:8]


def _past_question(row: ChatMessage, by_key: dict) -> str:
    """An older question: what it asked, and the names of what it asked about.

    Names, not blocks. The full detail of those states was in the transcript
    when the question was answered, and the answer that followed is still here
    -- re-sending the rows would pay for the same context once per turn and
    still be the *current* rows, not the ones that answer was based on.
    """
    keys = _loads(row.node_keys, [])
    if not keys:
        return row.content
    names = ", ".join(_name_of(k, by_key) for k in keys)
    return f"[attached: {names}]\n\n{row.content}"


def _current_question(
    question: str,
    attached_keys: list[str],
    run: Run | None,
    states: list[AppState],
    edges: list[StateTransition],
    verdicts: dict,
    by_key: dict,
    opening: bool,
    map_run_changed: bool,
) -> str:
    """The turn being asked now: the map, the attachments in full, the question.

    `opening` marks the first message of a thread, which carries the target too.
    Everything else here rides on the latest turn rather than the first because
    the map is *live* -- a crawl can finish while the thread is open, and a
    briefing pinned to turn one would answer turn six from a stale graph.
    """
    parts: list[str] = []

    if run is None or not states:
        parts.append(
            "## Map\n\nNo run has mapped this target yet, so there are no states "
            "to reason about. Say so plainly rather than guessing at what the "
            "application contains."
        )
    else:
        heading = f"## Map (run {run.id}, status {run.status})"
        if map_run_changed and not opening:
            # Silence here would be a contradiction the model has to resolve on
            # its own: the state names it used three turns ago belong to a graph
            # that is no longer the one in front of it.
            heading += (
                "\n\nThis is a **different run** from the one earlier in this "
                "conversation -- the application was re-crawled, so states named "
                "earlier may not exist on this map."
            )
        parts.append(
            f"{heading}\n\n"
            f"{len(states)} state(s), {len(edges)} transition(s)\n\n"
            + "\n".join(_state_line(s, verdicts.get(s.key)) for s in states)
        )

    attached = [by_key[k] for k in attached_keys if k in by_key]
    if attached:
        parts.append(
            "## Attached states\n\nThe person selected these on the canvas.\n\n"
            + "\n\n".join(
                _attached_block(s, verdicts.get(s.key), edges, by_key)
                for s in attached
            )
        )
        # Silence here would read as "that state has nothing interesting",
        # which is a different claim from "that state is not on this map".
        missing = [k for k in attached_keys if k not in by_key]
        if missing:
            parts.append(
                "Note: "
                + ", ".join(k[:8] for k in missing)
                + " was attached but is not on this run's map."
            )
    elif attached_keys:
        parts.append(
            "## Attached states\n\nThe attached states are not on this run's map "
            "-- they were selected against a different run. Answer about the map "
            "you have, and say that the selection does not match it."
        )
    else:
        parts.append(
            "## Attached states\n\nNone. The question is about the map as a whole."
        )

    parts.append(f"## Their question\n\n{question}")
    return "\n\n".join(parts)


def _build_transcript(
    target_url: str,
    run: Run | None,
    states: list[AppState],
    edges: list[StateTransition],
    verdicts: dict,
    history: list[ChatMessage],
    question: str,
    node_keys: list[str],
) -> Transcript:
    """History as alternating turns, with the live context on the last one.

    Roles must strictly alternate for every provider, and the rows cannot be
    trusted to: a deleted message or a thread adopted from the pre-thread world
    can leave two questions in a row. Same-role runs are merged rather than
    dropped -- losing a question the person actually asked is the worse failure.
    """
    by_key = {s.key: s for s in states}
    map_run_changed = any(
        m.run_id is not None and run is not None and m.run_id != run.id
        for m in history
    )

    turns: list[tuple[str, str]] = [
        (
            m.role,
            m.content if m.role == "assistant" else _past_question(m, by_key),
        )
        for m in history
    ]
    turns.append(
        (
            "user",
            _current_question(
                question=question,
                attached_keys=node_keys,
                run=run,
                states=states,
                edges=edges,
                verdicts=verdicts,
                by_key=by_key,
                opening=not history,
                map_run_changed=map_run_changed,
            ),
        )
    )

    merged: list[tuple[str, str]] = []
    for role, text in turns:
        if merged and merged[-1][0] == role:
            merged[-1] = (role, f"{merged[-1][1]}\n\n{text}")
        else:
            merged.append((role, text))

    # The target heads the opening message because it is the one fact that is
    # true of every turn and never changes.
    header = f"## Target\n\n{target_url}\n\n"
    if merged[0][0] != "user":  # defensive: a thread whose first row is a reply
        merged.insert(0, ("user", "(the opening question is no longer on record)"))
    prompt = header + merged[0][1]

    exchanges: list[Exchange] = []
    for role, text in merged[1:]:
        if role == "assistant":
            exchanges.append(Exchange(text=text))
        else:
            # A user turn answers the assistant turn before it. There is always
            # one, because the merge above guarantees alternation from index 1.
            last = exchanges[-1]
            exchanges[-1] = Exchange(
                text=last.text,
                calls=last.calls,
                results=last.results,
                opaque=last.opaque,
                follow_up=text,
            )

    return Transcript(prompt=prompt, exchanges=exchanges)


# --------------------------------------------------------------------------
# Threads
# --------------------------------------------------------------------------


def _session_or_404(session_id: int, db: Session) -> TestSession:
    row = db.get(TestSession, session_id)
    if row is None:
        raise HTTPException(404, "session not found")
    return row


def _thread_or_404(thread_id: int, db: Session) -> ChatThread:
    row = db.get(ChatThread, thread_id)
    if row is None:
        raise HTTPException(404, "thread not found")
    return row


@router.get("/api/sessions/{session_id}/chat/threads", response_model=list[ChatThread])
def list_threads(session_id: int, db: Session = Depends(get_session)):
    """Every thread, open or closed, oldest first.

    Closed ones are included deliberately: the console needs them to populate
    the "reopen" list, and a closed window that cannot be found again is a
    deleted one wearing a friendlier word.
    """
    _session_or_404(session_id, db)
    return db.exec(
        select(ChatThread)
        .where(ChatThread.session_id == session_id)
        .order_by(ChatThread.id)
    ).all()


@router.post(
    "/api/sessions/{session_id}/chat/threads",
    response_model=ChatThread,
    status_code=201,
)
def create_thread(
    session_id: int, body: ThreadCreate | None = None, db: Session = Depends(get_session)
):
    _session_or_404(session_id, db)
    thread = ChatThread(
        session_id=session_id, title=(body.title if body else None) or "New chat"
    )
    db.add(thread)
    db.commit()
    db.refresh(thread)
    return thread


@router.patch("/api/chat/threads/{thread_id}", response_model=ChatThread)
def patch_thread(
    thread_id: int, body: ThreadPatch, db: Session = Depends(get_session)
):
    """Rename, close, reopen, minimise, restore -- all one write."""
    thread = _thread_or_404(thread_id, db)
    if body.title is not None:
        title = body.title.strip()
        if not title:
            raise HTTPException(422, "a thread needs a name")
        thread.title = title
    if body.open is not None:
        thread.open = body.open
    if body.minimised is not None:
        thread.minimised = body.minimised
    db.add(thread)
    db.commit()
    db.refresh(thread)
    return thread


@router.delete("/api/chat/threads/{thread_id}", status_code=204)
def delete_thread(thread_id: int, db: Session = Depends(get_session)):
    """Destroy the thread and everything said in it. Closing is the soft one."""
    thread = _thread_or_404(thread_id, db)
    for row in db.exec(
        select(ChatMessage).where(ChatMessage.thread_id == thread_id)
    ):
        db.delete(row)
    db.delete(thread)
    db.commit()


@router.get(
    "/api/chat/threads/{thread_id}/messages", response_model=list[ChatMessage]
)
def list_messages(thread_id: int, db: Session = Depends(get_session)):
    """The whole thread, oldest first. Short enough not to need paging."""
    _thread_or_404(thread_id, db)
    return db.exec(
        select(ChatMessage)
        .where(ChatMessage.thread_id == thread_id)
        .order_by(ChatMessage.id)
    ).all()


@router.delete("/api/chat/threads/{thread_id}/messages", status_code=204)
def clear_messages(thread_id: int, db: Session = Depends(get_session)):
    """Empty a thread without closing the window. The map is untouched."""
    _thread_or_404(thread_id, db)
    for row in db.exec(
        select(ChatMessage).where(ChatMessage.thread_id == thread_id)
    ):
        db.delete(row)
    db.commit()


# --------------------------------------------------------------------------
# Asking
# --------------------------------------------------------------------------


def _latest_run_id(session_id: int, db: Session) -> int | None:
    row = db.exec(
        select(Run).where(Run.session_id == session_id).order_by(Run.id.desc())
    ).first()
    return row.id if row else None


@router.post("/api/chat/threads/{thread_id}/messages", response_model=ChatTurn)
def send(thread_id: int, body: ChatRequest, db: Session = Depends(get_session)):
    """Ask, and answer.

    Deliberately `def`, not `async def`: the provider call is blocking and takes
    seconds, so FastAPI running it in a worker thread is what keeps the console's
    two-second polls answering while the model thinks.

    **Nothing is written until the model answers.** Persisting the question first
    and the reply second reads better until the call fails -- and then the thread
    holds a question with no answer, which the next turn replays as context and
    which the user can only clear by asking again. Writing both together means a
    failure leaves the thread exactly as it was and the console can put the text
    back in the box.
    """
    thread = _thread_or_404(thread_id, db)
    session_id = thread.session_id
    target = db.get(TestSession, session_id) if session_id is not None else None
    if target is None:
        raise HTTPException(404, "session not found")

    question = body.text.strip()
    if not question:
        raise HTTPException(422, "message is empty")

    # The console sends the run it is showing. Falling back to the latest run
    # matters for the first message of a session, where the map pane has one
    # selected and the chat has not been told yet.
    run_id = body.run_id if body.run_id is not None else _latest_run_id(session_id, db)
    run = db.get(Run, run_id) if run_id is not None else None

    states: list[AppState] = []
    edges: list[StateTransition] = []
    verdicts: dict = {}
    if run is not None:
        states = list(db.exec(select(AppState).where(AppState.run_id == run.id)).all())
        edges = list(
            db.exec(select(StateTransition).where(StateTransition.run_id == run.id)).all()
        )
        verdicts = verdicts_by_state(run.id, db)

    history = list(
        db.exec(
            select(ChatMessage)
            .where(ChatMessage.thread_id == thread_id)
            .order_by(ChatMessage.id.desc())
            .limit(HISTORY_LIMIT)
        ).all()
    )[::-1]

    transcript = _build_transcript(
        target_url=target.target_url,
        run=run,
        states=states,
        edges=edges,
        verdicts=verdicts,
        history=history,
        question=question,
        node_keys=body.node_keys,
    )

    # Hoisted out of the `turn` call so the transcript written below is the one
    # the model actually answered. `transcript` is the multi-turn history built
    # above -- this used to be a fresh single-turn `Transcript(prompt=...)`, and
    # keeping that line here would have thrown the history away and raised
    # NameError besides.
    system = instructions("analyst")
    try:
        provider = load()
        turn = provider.turn(system, transcript, [])
    except Exception as exc:  # noqa: BLE001 -- the reason is the whole point
        # 502 rather than 500: the failure is almost always an absent or spent
        # API key, and `str(exc)` already says which. Swallowing it here is the
        # bug this codebase has fixed twice -- see `run.summary` and the console's
        # status disclosure.
        raise HTTPException(502, f"the model could not answer: {exc}") from exc

    # `ChatMessage` stores the answer and the `node_keys` the question was asked
    # about, which is enough to reconstruct roughly what the model saw and not
    # what it was told. The assembled prompt is several hundred lines of map and
    # the system prompt is edited constantly, so a thread that records neither
    # cannot explain its own answers a day later. Written before the 502 guard
    # below: an empty answer is the transcript most worth having.
    transcript.exchanges.append(
        Exchange(text=turn.text, calls=turn.calls, opaque=turn.opaque)
    )
    try:
        save_transcript(
            transcript,
            run_id=run.id if run else None,
            role="analyst",
            system=system,
            label=f"s{session_id}",
        )
    except Exception:
        # Losing the write-up must never lose the answer.
        pass

    answer = (turn.text or "").strip()
    if not answer:
        raise HTTPException(502, "the model returned an empty answer")

    keys = json.dumps(body.node_keys)
    user_row = ChatMessage(
        session_id=session_id,
        thread_id=thread_id,
        role="user",
        content=question,
        node_keys=keys,
        run_id=run.id if run else None,
    )
    assistant_row = ChatMessage(
        session_id=session_id,
        thread_id=thread_id,
        role="assistant",
        content=answer,
        node_keys=keys,
        run_id=run.id if run else None,
    )
    db.add(user_row)
    db.add(assistant_row)

    # A window named after nothing is a window you cannot pick out of four.
    # Only the first question names it, and only if nobody has renamed it --
    # a title that follows the latest question is a title that moves while
    # you are looking for it.
    if not history and thread.title == "New chat":
        thread.title = (
            question[:TITLE_CHARS].rstrip() + "…"
            if len(question) > TITLE_CHARS
            else question
        )
        db.add(thread)

    db.commit()
    db.refresh(user_row)
    db.refresh(assistant_row)
    db.refresh(thread)
    return ChatTurn(user=user_row, assistant=assistant_row, thread=thread)
