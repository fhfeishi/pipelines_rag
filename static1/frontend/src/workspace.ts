import { useEffect, useRef, useState } from "react";
import { restoreTurns, type Turn } from "./conversation";
import type { Options } from "./api";

export type Saved<T = Record<string, unknown>> = { id: string; revision: number; title: string; data: T; updated_at?: string; source_status?: string };
type SessionData = { turns: Turn[]; options: Options };
export async function workspaceRequest(path: string, body?: unknown) {
  const response = await fetch(path, body ? { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : undefined);
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "保存失败");
  return data;
}
export function useWorkspace(turns: Turn[], options: Options, setTurns: (turns: Turn[]) => void, setOptions: (options: Options) => void) {
  const [sessions, setSessions] = useState<Saved<SessionData>[]>([]);
  const [active, setActive] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [message, setMessage] = useState("正在恢复会话…");
  const revisions = useRef<Record<string, number>>({});
  const chain = useRef<Promise<void>>(Promise.resolve());
  const pending = useRef<Saved<SessionData> | null>(null);
  const failed = useRef(false);
  const hydrated = useRef(false);
  useEffect(() => {
    void workspaceRequest("/api/workspace/sessions").then((items: Saved<SessionData>[]) => {
      setSessions(items);
      for (const item of items) revisions.current[item.id] = item.revision;
      const selected = items.find(item => item.id === localStorage.getItem("static1-session")) ?? items[0];
      if (selected) { setActive(selected.id); setTurns(restoreTurns(selected.data.turns)); setOptions(selected.data.options); }
      else setActive(crypto.randomUUID());
      hydrated.current = true; setLoaded(true); setMessage("会话已恢复");
    }).catch(e => setMessage("会话恢复失败：" + e.message));
  }, []);
  function flush() {
    const snapshot = pending.current;
    pending.current = null;
    if (!snapshot) return chain.current;
    chain.current = chain.current.then(async () => {
      if (failed.current) return;
      setMessage("正在保存…");
      try {
        const saved = await workspaceRequest(`/api/workspace/sessions/${snapshot.id}`, { ...snapshot, revision: revisions.current[snapshot.id] ?? 0 });
        revisions.current[snapshot.id] = saved.revision;
        setSessions(old => [saved, ...old.filter(item => item.id !== saved.id)]);
        setMessage("已保存到本地服务");
      } catch (e) { failed.current = true; setMessage((e as Error).message + "；请先导出当前对话，再刷新"); }
    });
    return chain.current;
  }
  useEffect(() => {
    if (!loaded) return;
    const timer = setInterval(() => void flush(), 1000);
    return () => clearInterval(timer);
  }, [loaded]);
  useEffect(() => {
    if (!loaded || !hydrated.current || !active) return;
    localStorage.setItem("static1-session", active);
    if (!turns.length) return;
    pending.current = { id: active, revision: 0, title: turns[0].question.slice(0, 100), data: { turns, options } };
    const timer = setTimeout(() => void flush(), 500);
    return () => clearTimeout(timer);
  }, [turns, options, active, loaded]);
  async function select(id?: string) {
    await flush();
    if (failed.current) return;
    const item = sessions.find(s => s.id === id);
    setActive(item?.id ?? crypto.randomUUID());
    setTurns(item ? restoreTurns(item.data.turns) : []);
    if (item) setOptions(item.data.options);
  }
  return { sessions, active, loaded, message, select };
}
