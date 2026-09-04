/**
 * System Under Test.
 *
 * Self-healing test automation is impossible to demo without something that
 * breaks in a realistic way. `?v=2` and `?v=3` serve the same *functional* page
 * with drifted markup — renamed test ids, changed button copy, extra wrapper
 * elements, reordered fields. A locator written against v1 fails on v2/v3, and
 * the agent's job is to recover it.
 *
 * Keep the semantics identical across variants: only the selectors move.
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
  searchParams: Promise<{ v?: string }>;
}) {
  const raw = (await searchParams).v ?? "1";
  const variant: Variant = raw === "2" || raw === "3" ? raw : "1";
  const ids = FIELD[variant];

  // v3 also nests the form one level deeper, so structural locators break too.
  const Wrapper = variant === "3" ? "section" : "div";

  return (
    <main className="mx-auto flex min-h-dvh max-w-sm flex-col justify-center gap-6 p-6">
      <header>
        <h1 className="text-xl font-semibold">Acme Checkout</h1>
        <p className="text-sm text-neutral-500">DOM variant v{variant}</p>
      </header>

      <Wrapper className={variant === "3" ? "rounded-lg border p-4" : undefined}>
        <form className="flex flex-col gap-3" data-testid={`form-v${variant}`}>
          {/* v2 puts password first — order-based locators break here. */}
          {variant === "2" && (
            <PasswordField id={ids.password} className="order-first" />
          )}
          <label className="flex flex-col gap-1 text-sm">
            Email
            <input
              type="email"
              id={ids.email}
              name={ids.email}
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
        name={id}
        data-testid={id}
        className="rounded border border-neutral-300 px-2 py-1"
      />
    </label>
  );
}
