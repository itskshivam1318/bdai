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
  return <SessionView sessionId={sessionId} />;
}
