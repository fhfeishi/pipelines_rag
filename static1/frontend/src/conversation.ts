import type { Event, Message, Source, Options, Policy } from "./api.ts";

export type Attempt = {
  telemetry?: import("./api").Telemetry;
  usage?: import("./api").Usage;
  options: Options;
  policy: Policy | null;
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

export function newAttempt(options: Options = { query_routing: "auto", evidence_level: "middle", allowed_doc_ids: null }): Attempt {
  return { options: { ...options, allowed_doc_ids: options.allowed_doc_ids ? [...options.allowed_doc_ids] : null }, policy: null, answer: "", sources: [], complete: false, outcome: "running", startedAt: new Date().toISOString(), firstTokenMs: null, totalMs: null, elapsedMs: null };
}

export function newTurn(question: string, history: Turn[], options?: Options): Turn {
  const previous = history.filter(t => t.complete && t.outcome === "completed").slice(-9).flatMap(t => [
    { role: "user" as const, content: t.question },
    { role: "assistant" as const, content: t.answer },
  ]);
  return { ...newAttempt(options), question, requestMessages: [...previous, { role: "user", content: question }], previousAttempts: [] };
}

export function regenerateTurn(turn: Turn, history: Turn[]): Turn {
  const { question, requestMessages: _oldRequest, previousAttempts, ...attempt } = turn;
  const effective = turn.policy ?? turn.options;
  const options: Options = { ...(effective.execution_mode ? { execution_mode: effective.execution_mode } : {}), query_routing: effective.query_routing, evidence_level: effective.evidence_level, allowed_doc_ids: effective.allowed_doc_ids };
  return { ...newTurn(question, history, options), previousAttempts: [...previousAttempts, attempt] };
}

export function receiveEvent(turn: Turn, event: Event, elapsedMs: number): Turn {
  if (turn.outcome !== "running") return turn;
  if (event.event === "telemetry") return { ...turn, telemetry: event.data };
  if (event.event === "usage") return { ...turn, usage: event.data };
  if (event.event === "policy") return { ...turn, policy: event.data };
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

export function restoreTurns(turns: Turn[]): Turn[] {
  return turns.map(turn => ({ ...stopTurn(turn, true, turn.elapsedMs ?? 0),
    previousAttempts: turn.previousAttempts.map(attempt => attempt.outcome === "running" ? { ...attempt, outcome: "cancelled", complete: false } : attempt) }));
}
