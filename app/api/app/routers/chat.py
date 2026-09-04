"""Ask the map a question, with states attached from the canvas.

    GET  /api/sessions/{id}/chat
    POST /api/sessions/{id}/chat   {"text": "...", "node_keys": [...], "run_id": 3}

**Why this is not the intent box.** The bar at the bottom of the console used to
write `Intent: <text>` onto the run timeline and stop -- nothing read it back and
nothing replied, so a control shaped exactly like a chat did not chat. This is
the other half: the states the user selected on the map become context, and a
model answers about them.

**Why the context is assembled here rather than by a tool call.** The model gets
one shot and no tools. Everything it could ask for -- the map, the attached
states in full, the thread -- is small enough to hand over up front, and a
read-only question does not need a loop that could decide to crawl something.
`agents/orchestrator.py` is where tool-calling belongs; this is a reader.

**Why the whole thread is rebuilt into `Transcript.prompt` each time.** The
neutral `Transcript` in `agents/llm` models an *agent* loop -- one opening prompt
followed by exchanges of assistant turn plus tool results -- and has nowhere to
put a second user message. Widening that dataclass to carry chat turns would
change a contract the ants depend on, to serve a surface with no tools. Rendering
the thread into the prompt costs a few hundred tokens and touches nothing the
colony uses.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from agents.ant import instructions
from agents.llm import Transcript, load
from agents.suite import verdicts_by_state

from ..db import get_session
from ..models import AppState, ChatMessage, Run, StateTransition, TestSession

router = APIRouter(prefix="/api/sessions", tags=["chat"])

# The thread is replayed in full on every turn, so it needs a bound. Twenty
# messages is roughly ten exchanges -- past that a question is almost always
# about something recent, and the map itself is re-sent every time anyway.
HISTORY_LIMIT = 20


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
    answers.
    """

    user: ChatMessage
    assistant: ChatMessage


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
            origin = (
                source.label or source.title if source else edge.from_key[:8]
            )
            lines.append(f"  - {edge.action} from {origin}")

    return "\n".join(lines)


def _build_prompt(
    target_url: str,
    run: Run | None,
    states: list[AppState],
    edges: list[StateTransition],
    verdicts: dict,
    attached_keys: list[str],
    history: list[ChatMessage],
    question: str,
) -> str:
    by_key = {s.key: s for s in states}
    parts = [f"## Target\n\n{target_url}"]

    if run is None or not states:
        parts.append(
            "## Map\n\nNo run has mapped this target yet, so there are no states "
            "to reason about. Say so plainly rather than guessing at what the "
            "application contains."
        )
    else:
        parts.append(
            f"## Map (run {run.id}, status {run.status})\n\n"
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

    if history:
        parts.append(
            "## Conversation so far\n\n"
            + "\n\n".join(
                f"{'Them' if m.role == 'user' else 'You'}: {m.content}"
                for m in history
            )
        )

    parts.append(f"## Their question\n\n{question}")
    return "\n\n".join(parts)




def _latest_run_id(session_id: int, db: Session) -> int | None:
    row = db.exec(
        select(Run).where(Run.session_id == session_id).order_by(Run.id.desc())
    ).first()
    return row.id if row else None


@router.get("/{session_id}/chat", response_model=list[ChatMessage])
def list_chat(session_id: int, db: Session = Depends(get_session)):
    """The whole thread, oldest first. Short enough not to need paging."""
    if db.get(TestSession, session_id) is None:
        raise HTTPException(404, "session not found")
    return db.exec(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.id)
    ).all()


@router.post("/{session_id}/chat", response_model=ChatTurn)
def send(session_id: int, body: ChatRequest, db: Session = Depends(get_session)):
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
    target = db.get(TestSession, session_id)
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
        states = list(
            db.exec(select(AppState).where(AppState.run_id == run.id)).all()
        )
        edges = list(
            db.exec(
                select(StateTransition).where(StateTransition.run_id == run.id)
            ).all()
        )
        verdicts = verdicts_by_state(run.id, db)

    history = list(
        db.exec(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.id.desc())
            .limit(HISTORY_LIMIT)
        ).all()
    )[::-1]

    prompt = _build_prompt(
        target_url=target.target_url,
        run=run,
        states=states,
        edges=edges,
        verdicts=verdicts,
        attached_keys=body.node_keys,
        history=history,
        question=question,
    )

    try:
        provider = load()
        turn = provider.turn(instructions("analyst"), Transcript(prompt=prompt), [])
    except Exception as exc:  # noqa: BLE001 -- the reason is the whole point
        # 502 rather than 500: the failure is almost always an absent or spent
        # API key, and `str(exc)` already says which. Swallowing it here is the
        # bug this codebase has fixed twice -- see `run.summary` and the console's
        # status disclosure.
        raise HTTPException(502, f"the model could not answer: {exc}") from exc

    answer = (turn.text or "").strip()
    if not answer:
        raise HTTPException(502, "the model returned an empty answer")

    keys = json.dumps(body.node_keys)
    user_row = ChatMessage(
        session_id=session_id,
        role="user",
        content=question,
        node_keys=keys,
        run_id=run.id if run else None,
    )
    assistant_row = ChatMessage(
        session_id=session_id,
        role="assistant",
        content=answer,
        node_keys=keys,
        run_id=run.id if run else None,
    )
    db.add(user_row)
    db.add(assistant_row)
    db.commit()
    db.refresh(user_row)
    db.refresh(assistant_row)
    return ChatTurn(user=user_row, assistant=assistant_row)


@router.delete("/{session_id}/chat", status_code=204)
def clear_chat(session_id: int, db: Session = Depends(get_session)):
    """Start the thread over. The map is untouched."""
    if db.get(TestSession, session_id) is None:
        raise HTTPException(404, "session not found")
    for row in db.exec(
        select(ChatMessage).where(ChatMessage.session_id == session_id)
    ):
        db.delete(row)
    db.commit()
