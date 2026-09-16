export type Source = { title: string; url: string; snippet: string; page?: number; doc_id?: string; version?: string; origin?: string; kind?: string };
export type Message = { role: "user" | "assistant"; content: string };
export type Event =
  | { event: "status"; data: { message: string } }
  | { event: "sources"; data: Source[] }
  | { event: "token"; data: { text: string } }
  | { event: "done"; data: { ok: boolean } }
  | { event: "error"; data: { message: string } };

export async function streamChat(messages: Message[], signal: AbortSignal, receive: (event: Event) => void) {
  const response = await fetch("/api/chat", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ messages }), signal,
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(typeof payload?.detail === "string" ? payload.detail : "请求失败（" + response.status + "）");
  }
  if (!response.body) throw new Error("浏览器未收到响应流");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finished = false;
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      let end: number;
      while ((end = buffer.indexOf("\n\n")) >= 0) {
        const frame = buffer.slice(0, end);
        buffer = buffer.slice(end + 2);
        const event = frame.split("\n").find(line => line.startsWith("event:"))?.slice(6).trim();
        const data = frame.split("\n").filter(line => line.startsWith("data:")).map(line => line.slice(5).trimStart()).join("\n");
        if (!event || !data) continue;
        const payload = JSON.parse(data);
        if (event === "error") throw new Error(payload.message);
        if (event === "done") {
          if (!payload.ok) throw new Error("回答未完成");
          finished = true;
        }
        if (["status", "sources", "token", "done"].includes(event)) {
          receive({ event, data: payload } as Event);
        }
        if (finished) return;
      }
      if (done) break;
    }
    if (!finished) throw new Error("连接中断，回答未完成");
  } finally {
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}
