import type { ReactNode } from "react";
import Sidebar from "@/components/Sidebar";

/**
 * Console chrome. Scoped to a route group so `/sut` — our own system under
 * test — renders bare, with no sidebar leaking into the app the agent drives.
 */
export default function ConsoleLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-dvh">
      <Sidebar />
      <main className="flex min-w-0 flex-1 flex-col">{children}</main>
    </div>
  );
}
