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


class CanvasNode(SQLModel, table=True):
    """Persisted position + config of one widget on the canvas."""

    id: Optional[int] = Field(default=None, primary_key=True)
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
    status: str = "pending"  # pending | passed | failed | healed
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


class Event(SQLModel, table=True):
    """Agent timeline entry -- what the canvas streams to show reasoning."""

    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: Optional[int] = Field(default=None, foreign_key="run.id", index=True)
    level: str = "info"  # info | warn | error | decision
    message: str
    created_at: datetime = Field(default_factory=utcnow)
