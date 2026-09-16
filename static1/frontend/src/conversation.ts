import type { Event, Message, Source } from "./api.ts";

export type Attempt = {
  answer: string;
  sources: Source[];
  complete: boolean;
  outcome: "running" | "completed" | "cancelled" | "failed";
  startedAt: string;
  firstTokenMs: number | null;
  totalMs: number | null;
  elapsedMs: number | null;
};
export type Turn = Attempt & {
  question: string;
  requestMessages: Message[];
  previousAttempts: Attempt[];
};

export function newAttempt(): Attempt {
  return { answer: "", sources: [], complete: false, outcome: "running", startedAt: new Date().toISOString(), firstTokenMs: null, totalMs: null, elapsedMs: null };
}

export function newTurn(question: string, history: Turn[]): Turn {
  const previous = history.filter(t => t.complete && t.outcome === "completed").slice(-9).flatMap(t => [
    { role: "user" as const, content: t.question },
    { role: "assistant" as const, content: t.answer },
  ]);
  return { ...newAttempt(), question, requestMessages: [...previous, { role: "user", content: question }], previousAttempts: [] };
}

export function regenerateTurn(turn: Turn, history: Turn[]): Turn {
  const { question, requestMessages: _oldRequest, previousAttempts, ...attempt } = turn;
  return { ...newTurn(question, history), previousAttempts: [...previousAttempts, attempt] };
}

export function receiveEvent(turn: Turn, event: Event, elapsedMs: number): Turn {
  if (turn.outcome !== "running") return turn;
  if (event.event === "sources") return { ...turn, sources: event.data };
  if (event.event === "token" && event.data.text) {
    return { ...turn, answer: turn.answer + event.data.text, firstTokenMs: turn.firstTokenMs ?? elapsedMs };
  }
  if (event.event === "done" && event.data.ok) {
    return { ...turn, complete: true, outcome: "completed", totalMs: elapsedMs, elapsedMs };
  }
  return turn;
}

export function stopTurn(turn: Turn, cancelled: boolean, elapsedMs: number): Turn {
  if (turn.outcome !== "running") return turn;
  return { ...turn, complete: false, outcome: cancelled ? "cancelled" : "failed", totalMs: null, elapsedMs };
}

export function formatDuration(ms: number): string {
  return `${(ms / 1000).toFixed(1)} 秒`;
}
