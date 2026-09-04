import { notFound } from "next/navigation";
import SessionView from "@/components/SessionView";

export default async function SessionPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const sessionId = Number(id);
  if (!Number.isInteger(sessionId)) notFound();
  // Navigation between sessions is client-side, so without a key React
  // reuses the same SessionView instance across a session switch: the
  // previous session's selected run and StageRail's accumulated events
  // would bleed through instead of resetting.
  return <SessionView key={sessionId} sessionId={sessionId} />;
}
