"""Database models.

These are deliberately generic. Tomorrow's problem statement will sharpen them,
but every autonomous-QA framing needs the same spine: you point a run at a
target, it produces evidence (screenshots, DOM snapshots, diffs), and it emits a
timeline of what the agent did. Widening a column tomorrow is cheap; discovering
you have nowhere to put evidence at hour 6 is not.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TestSession(SQLModel, table=True):
    """One target URL and everything the agent has ever done against it.

    A session outlives any single run: re-running after the app changes is what
    the healer story is *about*, so both runs have to live side by side under
    one canvas rather than in two unrelated histories.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    target_url: str
    # Null until someone renames it; the UI shows "Untitled session".
    name: Optional[str] = None
    # What the tester typed beside the URL, verbatim: credentials, focus, and
    # statements they want checked, all in one box. Parsed per run by
    # `agents.context.parse` rather than on the way in, because the parse needs
    # a model and the model is chosen at explore time, not at session creation.
    #
    # Stored as typed, including passwords, and that is a decision rather than
    # an oversight -- this is a hackathon tool pointed at test accounts, and
    # encrypting one column while the rest of the row is plaintext would claim a
    # posture nothing here has.
    #
    # It is deliberately *not* justified by "the snapshot leaks it anyway".
    # That was true when this column was added and is being fixed on
    # `work/agent-forensics` (cac872e redacts credentials inside `observe()`, so
    # they stop reaching `StateObservation` at all). Once that lands this column
    # is the one place a password is kept on purpose -- which is the thing to
    # know before anyone points this at something real.
    context: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)


class CanvasNode(SQLModel, table=True):
    """Persisted position + config of one widget on the canvas."""

    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: Optional[int] = Field(
        default=None, foreign_key="testsession.id", index=True
    )
    widget_type: str
    x: float = 0.0
    y: float = 0.0
    width: Optional[float] = None
    height: Optional[float] = None
    # JSON blob of widget-specific settings, kept schemaless on purpose.
    config: str = "{}"
    created_at: datetime = Field(default_factory=utcnow)


class Run(SQLModel, table=True):
    """One execution of the QA agent against a target."""

    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: Optional[int] = Field(
        default=None, foreign_key="testsession.id", index=True
    )
    target_url: str
    status: str = "pending"  # pending | running | passed | failed | error
    summary: Optional[str] = None
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: Optional[datetime] = None


class TestCase(SQLModel, table=True):
    """A single check within a run.

    `selector` and `healed_selector` are the self-healing story: when the
    original locator stops matching, the agent records what it fell back to.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: Optional[int] = Field(default=None, foreign_key="run.id", index=True)
    name: str
    selector: Optional[str] = None
    healed_selector: Optional[str] = None
    # JSON list of the state keys this scenario crosses, in order. The map
    # colours a node by the worst verdict among the scenarios naming it, so
    # this is the join between a test result and a place on the graph.
    path: str = "[]"
    status: str = "pending"  # pending | passed | failed | healed | defect | escalate
    detail: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)


class Artifact(SQLModel, table=True):
    """Evidence produced during a run: screenshot, DOM dump, visual diff."""

    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: Optional[int] = Field(default=None, foreign_key="run.id", index=True)
    kind: str  # screenshot | dom | diff | trace
    path: str  # relative to the artifacts dir, served at /artifacts/<path>
    label: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)


class StateObservation(SQLModel, table=True):
    """One look at the application: the evidence behind a state or an edge.

    The raw accessibility snapshot is kept verbatim and never summarised. It is
    what `statekey.explain()` needs to say *why* two states differ, what the
    Healer re-reads to tell a broken locator from a broken app, and the only
    thing here that cannot be recomputed later. States and transitions are
    derived; this is the primary record.

    Rows are written in the order observed, so a row's position within its run
    matches the index that `WorldMap.evidence` uses in memory.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: Optional[int] = Field(default=None, foreign_key="run.id", index=True)
    state_key: str = Field(index=True)
    url: str
    title: str
    snapshot: str  # raw AI-mode aria snapshot
    network: str = "[]"  # JSON list of {method, url, status, resource_type}
    captured_at: datetime = Field(default_factory=utcnow)


class AppState(SQLModel, table=True):
    """One behavioural state of the application under test.

    `key` is the `state_key` digest -- identity is what `normalize()` kept, never
    the URL. Two routes can be one state and one route can be two, which is the
    whole reason this table exists instead of a `pages` table.

    Scoped to a **run**, not a session, so that re-crawling after the app
    changes produces a second map beside the first rather than overwriting it.
    Comparing two runs' maps is the drift story the healer is about.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: Optional[int] = Field(default=None, foreign_key="run.id", index=True)
    key: str = Field(index=True)
    url: str  # first seen; descriptive only, never identity
    title: str
    actions: str = "[]"  # JSON list of action descriptors
    # JSON list of [role, name] pairs -- the fillable fields this screen
    # offers. Derived from the state's first observation at save time, not
    # stored on StateNode: `worldmap.py` takes `actions_of` injected precisely
    # so that it never learns what a control means.
    fields: str = "[]"
    # Human-readable name. A model seam -- null until something names it.
    label: Optional[str] = None
    # Path to one screenshot, relative to the artifacts dir, served at
    # /artifacts/<path>. Null when capture was off or the shot failed.
    screenshot: Optional[str] = None
    # Which ant first reached this state, e.g. "w2a1". Null for a model-free
    # crawl, which has no ants to attribute to.
    found_by: Optional[str] = None
    is_entry: bool = False
    first_seen: datetime = Field(default_factory=utcnow)


class StateTransition(SQLModel, table=True):
    """Taking one action from one state landed in another. Or the same one.

    Deliberately not unique on (run_id, from_key, action): the same action from
    the same state landing somewhere *else* is the signal that `normalize()`
    collapsed two states that behave differently, and storing both rows is what
    makes that contradiction visible instead of silently overwritten.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: Optional[int] = Field(default=None, foreign_key="run.id", index=True)
    from_key: str = Field(index=True)
    action: str
    to_key: str = Field(index=True)
    # A POST/PUT/DELETE fired during this action. With `from_key == to_key` it
    # separates "the click did nothing" from "the app accepted it and did not
    # re-render" -- the distinction the Healer classifies on.
    mutating: bool = False
    # The ant that took this action.
    found_by: Optional[str] = None
    observation_id: Optional[int] = Field(
        default=None, foreign_key="stateobservation.id"
    )
    created_at: datetime = Field(default_factory=utcnow)


class Event(SQLModel, table=True):
    """Agent timeline entry -- what the canvas streams to show reasoning.

    `surface` is how the agent asks for something to be shown without knowing
    that widgets exist: it names *what deserves attention* ("coverage"), and the
    frontend decides which widget that is, how big it is, and where it goes.
    An event with no `surface` is an ordinary log line.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: Optional[int] = Field(default=None, foreign_key="run.id", index=True)
    level: str = "info"  # info | warn | error | decision
    message: str
    # Semantic key, not a widget type -- see web/lib/widgets/surfaces.ts.
    surface: Optional[str] = None
    # Opaque pointer the widget resolves, e.g. "testcase:12".
    ref: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)


class ChatThread(SQLModel, table=True):
    """One chat window: a conversation with its own history and its own state.

    Threads exist because one question is rarely one subject. "Why did sign-in
    split in two" and "what has no coverage" are two investigations, and running
    them down a single transcript makes each the other's noise -- the model
    carries the first into the second, and the person scrolls past one to read
    the other.

    Scoped to the **session**, like the messages in it, and for the same reason:
    the investigation outlives the crawl that provoked it.

    `open` and `minimised` are window state, deliberately on the row rather than
    in the browser. A console reloaded mid-demo that comes back with an empty
    right margin has lost work that looks like it was never there; closing a
    window is meant to mean "put this away", and putting something away implies
    finding it again. Closed is not deleted -- `DELETE /api/chat/threads/{id}`
    is the only thing that destroys a thread.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: Optional[int] = Field(
        default=None, foreign_key="testsession.id", index=True
    )
    # Derived from the first question and editable after. Not model-generated:
    # a second API call to name a window costs a real second on the one
    # interaction that is already the slowest thing in the console.
    title: str = "New chat"
    open: bool = True
    minimised: bool = False
    created_at: datetime = Field(default_factory=utcnow)


class ChatMessage(SQLModel, table=True):
    """One turn of the conversation held beside a session's map.

    Scoped to the **session**, not a run: the question "why did sign-in split
    into two states" outlives the run that provoked it, and re-crawling should
    not wipe the thread that explains the last crawl.

    `node_keys` is the JSON list of `AppState.key` values the user attached when
    they sent the message -- the map nodes they had selected. Stored on the row
    rather than resolved at send time because it is *what was asked about*: the
    same question with two different states attached is two different questions,
    and re-reading the thread later has to show which.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: Optional[int] = Field(
        default=None, foreign_key="testsession.id", index=True
    )
    # Which conversation this belongs to. Nullable only because rows written
    # before threads existed have no answer -- `db.adopt_orphan_chat()` gives
    # them one at startup, so nothing live should carry a null.
    thread_id: Optional[int] = Field(
        default=None, foreign_key="chatthread.id", index=True
    )
    role: str  # user | assistant
    content: str
    node_keys: str = "[]"
    # Which run's map the attached keys belong to. A key is only unique within
    # a run, so without this an old message's chips resolve against the wrong
    # graph after a re-crawl.
    run_id: Optional[int] = Field(default=None, foreign_key="run.id")
    created_at: datetime = Field(default_factory=utcnow)


class SkippedAction(SQLModel, table=True):
    """An action a state offered that the crawler tried and could not take.

    The distinction this table exists to make: a refused action is **not**
    unexplored. `worldmap.summary()` has always said so in prose -- "these are
    NOT unexplored, they were tried and could not be done" -- but the fact lived
    only on the in-memory map, so anything reading the database counted a login
    wall it could not fill as frontier it had not reached yet.

    That is the difference between a progress number that can reach 100% and one
    that structurally cannot. `reason` is one of the three `crawler.py` records,
    verbatim, because *why* it was refused is the useful half: "nothing here
    could be filled" on a sign-in form is usually the most expensive fact in the
    whole crawl.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: Optional[int] = Field(default=None, foreign_key="run.id", index=True)
    state_key: str = Field(index=True)
    action: str
    reason: str
    created_at: datetime = Field(default_factory=utcnow)
