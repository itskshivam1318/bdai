"""What the user typed beside the URL, and what the run may do with it.

    Context(raw=...)  ->  credentials + focus + claims

The brief requires the URL to be the *only* required input, and it still is.
This is the optional second field: one textarea holding whatever a tester would
have said to a colleague handing over an app -- "log in as standard_user /
secret_sauce, focus on checkout, and check that an out-of-stock item can't be
added to the cart".

**Three kinds of thing in one box, on purpose.** Nobody thinks in terms of
"credentials, focus and claims"; they think in terms of what they know about the
app. Splitting the box into three inputs would push that taxonomy onto the
person typing. Telling them apart is a job, and a model does it -- which is the
one thing in here that is not deterministic, and the reason `parse` takes a
provider.

**With no provider, nothing is guessed.** A regex that pulls "standard_user /
secret_sauce" out of prose also pulls the wrong halves out of a password
containing a slash, and it does it silently -- into a login form, on someone
else's staging server. So the no-model path returns an empty `Context` that
still carries `raw`, and the caller falls back to `Credentials.from_env()` and
says out loud that it did. This mirrors what the console already does with the
intent box, which is ignored with a warning when no model is configured.

**Nothing here is a secret from the database.** `test_session.context` stores
this text as typed, passwords included, and that is a decision rather than an
oversight -- this is a hackathon tool pointed at test accounts. It is
deliberately not justified by "the crawler leaks it anyway": that was true when
this was written and is being fixed on `work/agent-forensics`, where cac872e
redacts credentials inside `observe()` so they stop reaching
`StateObservation` at all. `redacted` here exists for one narrower reason --
the `Event` timeline is what is on screen during a demo.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .explorer.forms import Credentials
from .llm import Tool, Transcript

# One tool, one shape. The model's whole job is to sort the sentence fragments
# into four fields, so there is nothing here it can answer in prose -- and if it
# tries, `parse` keeps the empty Context rather than reading the prose back.
RECORD = Tool(
    name="record_context",
    description=(
        "Record what the tester said about this application, sorted into the "
        "fields below. Copy values verbatim from their text. Omit any field "
        "they did not actually mention -- never invent a credential, and never "
        "supply a placeholder."
    ),
    parameters={
        "type": "object",
        "properties": {
            "username": {
                "type": "string",
                "description": (
                    "The username, email or account to log in as, exactly as "
                    "written. Omit if they gave no login."
                ),
            },
            "password": {
                "type": "string",
                "description": (
                    "The password to log in with, exactly as written, "
                    "including punctuation. Omit if they gave none."
                ),
            },
            "focus": {
                "type": "string",
                "description": (
                    "Where they want the exploration to spend its effort -- "
                    "areas, flows or parts of the app. Prose, not a list. Omit "
                    "if they only gave credentials."
                ),
            },
            "claims": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Specific statements they want checked, one per entry, "
                    "each a single testable sentence. A claim asserts that "
                    "something should or should not happen. 'Focus on "
                    "checkout' is a focus, not a claim; 'a discount code "
                    "cannot be applied twice' is a claim."
                ),
            },
        },
        "required": [],
    },
)

SYSTEM = (
    "You sort a tester's handover note into structured fields by calling "
    "record_context exactly once. You are not testing anything and not "
    "answering the tester. Copy their words; do not improve them. If the note "
    "contains no credentials, no focus or no claims, leave those fields out "
    "rather than filling them with something plausible."
)


@dataclass(frozen=True)
class Context:
    """The parsed box. Every field optional; `raw` is the only one always true.

    Frozen for the same reason as `byok.Choice`: this is read by a background
    task that outlives the request that built it.
    """

    raw: str = ""
    credentials: Credentials = field(default_factory=Credentials)
    focus: str | None = None
    claims: tuple[str, ...] = ()
    # Whether a model actually read the box. An unparsed context and one the
    # model read and found nothing in are both empty, and they mean opposite
    # things: the first is a transient failure worth retrying, the second is a
    # note about what was typed. Telling a user to fix a box that was fine is
    # the mistake this exists to prevent.
    parsed: bool = False

    def __bool__(self) -> bool:
        return bool(self.raw.strip())

    @property
    def redacted(self) -> str:
        """One line for the timeline. Says what was understood, not the secret.

        The username is shown and the password is not, which is the same split
        every login screen makes: one of them identifies the run, and one of
        them is the thing you would not want on a screenshot.
        """
        if not self:
            return "no context"
        parts = []
        if self.credentials.username:
            parts.append(f"credentials for {self.credentials.username}")
        elif self.credentials.password:
            parts.append("a password with no username")
        if self.focus:
            parts.append(f"focus: {self.focus}")
        if self.claims:
            parts.append(f"{len(self.claims)} claim(s)")
        return ", ".join(parts) or "nothing usable"


def parse(raw: str, provider=None) -> Context:
    """Sort the box into fields. One model call, or none.

    Returns a `Context` carrying `raw` in every path, including the ones where
    the model refused, answered in prose, or was never there. A caller can
    always tell "they typed nothing" from "they typed something we could not
    use" -- and the second of those is worth a warning on the timeline.
    """
    text = (raw or "").strip()
    if not text:
        return Context(raw="")
    if provider is None:
        return Context(raw=text)

    try:
        turn = provider.turn(SYSTEM, Transcript(prompt=_brief(text)), [RECORD])
    except Exception:
        # A parse that fails costs the context, not the run. Everything behind
        # this call -- the crawl, the map, the suite -- needs no model at all,
        # and a rate limit on one small request must not take down a run that
        # would otherwise produce a complete deterministic map. The caller sees
        # an unparsed `Context` and says so on the timeline.
        return Context(raw=text)

    call = next((c for c in turn.calls if c.name == RECORD.name), None)
    if call is None:
        return Context(raw=text)

    return Context(
        raw=text,
        parsed=True,
        credentials=Credentials(
            username=_clean(call.arguments.get("username")),
            password=_clean(call.arguments.get("password")),
        ),
        focus=_clean(call.arguments.get("focus")),
        claims=tuple(
            cleaned
            for entry in call.arguments.get("claims") or ()
            if (cleaned := _clean(entry))
        ),
    )


def credentials_for(context: Context | None) -> Credentials:
    """What to type at a login wall: the box if it named one, else the machine.

    **All or nothing, never a mixture.** If the box supplied any part of a
    login it supplies the whole of it, and a missing half stays missing. The
    alternative -- taking the username from the box and the password from
    `AIVAR_PASSWORD` -- assembles a credential no human ever wrote down, and
    then submits it to somebody's login form. A failed login is a recoverable,
    legible outcome; an invented one is a lockout with nothing in the timeline
    that explains it.
    """
    if context and context.credentials:
        return context.credentials
    return Credentials.from_env()


def _brief(text: str) -> str:
    return (
        "A tester was asked what else we should know about the application "
        "under test, besides its URL. They wrote:\n\n"
        f"{text}\n\n"
        "Call record_context once with whatever of that is actually there."
    )


def _clean(value) -> str | None:
    """Empty string and whitespace mean absent, not present-and-blank.

    A model asked to omit a field it has nothing for will sometimes send `""`
    instead, and an empty username that reads as *supplied* would shadow the
    environment fallback with nothing.
    """
    if not isinstance(value, str):
        return None
    return value.strip() or None
