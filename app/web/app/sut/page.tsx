/**
 * System Under Test.
 *
 * Two independent knobs, because the demo has to tell two failures apart and a
 * fixture that conflates them proves nothing:
 *
 *   ?v=1|2|3   MARKUP DRIFT. Same behaviour, moved selectors — renamed test
 *              ids, changed button copy, reordered fields, an extra wrapper.
 *              A locator written against v1 misses on v2/v3. The agent should
 *              heal and carry on.
 *
 *   ?bug=1     BEHAVIOURAL DEFECT. Markup untouched, so every locator still
 *              resolves and the click still lands. The app simply fails to
 *              transition: a completed form returns the completed form instead
 *              of the confirmation. The agent must NOT heal this — there is
 *              nothing broken to repair — it must report it.
 *
 * Keep those orthogonal. Drift must never change behaviour and the bug must
 * never change markup, or an agent that scores well is only guessing.
 *
 * Rendered on the server and driven by a plain GET form, so a state is a URL
 * and every state the agent reaches is one a human can reach by hand.
 */

type Variant = "1" | "2" | "3";

const FIELD = {
  "1": { email: "email", password: "password", submit: "submit" },
  "2": { email: "user-email", password: "user-password", submit: "submit-btn" },
  "3": { email: "login_email", password: "login_password", submit: "action" },
} as const;

const SUBMIT_COPY: Record<Variant, string> = {
  "1": "Sign in",
  "2": "Log in",
  "3": "Continue",
};

export default async function SystemUnderTest({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  const one = (key: string) => {
    const value = params[key];
    return (Array.isArray(value) ? value[0] : value) ?? "";
  };

  const raw = one("v") || "1";
  const variant: Variant = raw === "2" || raw === "3" ? raw : "1";
  const ids = FIELD[variant];
  const buggy = one("bug") === "1";

  const submitted = one("submitted") === "1";
  const email = one("email").trim();
  const password = one("password").trim();
  const complete = Boolean(email && password);

  // The three outcomes. `buggy` collapses the success arm back onto the form —
  // no error, no confirmation, no clue in the markup. Exactly the failure a
  // selector-healing agent will happily "repair" forever if it is only
  // watching locators.
  const outcome =
    !submitted ? "form"
    : !complete ? "rejected"
    : buggy ? "form"
    : "confirmed";

  if (outcome === "confirmed") {
    return (
      <main className="mx-auto flex min-h-dvh max-w-sm flex-col justify-center gap-6 p-6">
        <header>
          <h1 className="text-xl font-semibold">Order confirmed</h1>
          <p className="text-sm text-neutral-500">DOM variant v{variant}</p>
        </header>
        <p className="text-sm">Signed in as {email}. Your order is on its way.</p>
        <a className="text-sm underline" href={`/sut?v=${variant}`}>
          Start over
        </a>
      </main>
    );
  }

  // v3 also nests the form one level deeper, so structural locators break too.
  const Wrapper = variant === "3" ? "section" : "div";

  return (
    <main className="mx-auto flex min-h-dvh max-w-sm flex-col justify-center gap-6 p-6">
      <header>
        <h1 className="text-xl font-semibold">Acme Checkout</h1>
        <p className="text-sm text-neutral-500">DOM variant v{variant}</p>
      </header>

      {outcome === "rejected" && (
        <p role="alert" className="text-sm text-red-600">
          Email and password are required
        </p>
      )}

      <Wrapper className={variant === "3" ? "rounded-lg border p-4" : undefined}>
        <form method="get" action="/sut" className="flex flex-col gap-3">
          {/* The knobs ride along so a submit stays in the world it started in. */}
          <input type="hidden" name="v" value={variant} />
          {buggy && <input type="hidden" name="bug" value="1" />}
          <input type="hidden" name="submitted" value="1" />

          {/* v2 puts password first — order-based locators break here. */}
          {variant === "2" && (
            <PasswordField id={ids.password} className="order-first" />
          )}
          <label className="flex flex-col gap-1 text-sm">
            Email
            <input
              type="email"
              id={ids.email}
              name="email"
              data-testid={ids.email}
              className="rounded border border-neutral-300 px-2 py-1"
              placeholder="you@example.com"
            />
          </label>
          {variant !== "2" && <PasswordField id={ids.password} />}
          <button
            type="submit"
            id={ids.submit}
            data-testid={ids.submit}
            className={
              variant === "1" ? "btn-primary rounded bg-black px-3 py-2 text-white"
              : "primary-button rounded bg-black px-3 py-2 text-white"
            }
          >
            {SUBMIT_COPY[variant]}
          </button>
        </form>
      </Wrapper>

      <nav className="flex gap-3 text-xs text-neutral-500">
        <a href="/sut?v=1">v1</a>
        <a href="/sut?v=2">v2</a>
        <a href="/sut?v=3">v3</a>
      </nav>
    </main>
  );
}

function PasswordField({ id, className }: { id: string; className?: string }) {
  return (
    <label className={`flex flex-col gap-1 text-sm ${className ?? ""}`}>
      Password
      <input
        type="password"
        id={id}
        name="password"
        data-testid={id}
        className="rounded border border-neutral-300 px-2 py-1"
      />
    </label>
  );
}
