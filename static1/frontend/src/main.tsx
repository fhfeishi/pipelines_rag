import { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { streamChat, type Message, type Source } from "./api";
import "./style.css";
import { KnowledgePanel } from "./KnowledgePanel";

type Turn = { question: string; answer: string; sources: Source[]; complete: boolean };
function App() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [health, setHealth] = useState("正在连接");
  const controller = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  useEffect(() => {
    fetch("/api/health").then(r => { if (!r.ok) throw new Error(); return r.json(); })
      .then(data => setHealth(data.api_key_configured ? "服务已连接 · " + data.model : "请配置后端模型密钥"))
      .catch(() => setHealth("后端未连接 · 请启动 FastAPI"));
    return () => controller.current?.abort();
  }, []);
  useEffect(() => { bottom.current?.scrollIntoView({ behavior: "smooth" }); }, [turns, status]);
  async function send() {
    const question = input.trim();
    if (!question || controller.current) return;
    const previous: Message[] = turns.filter(t => t.complete).slice(-9).flatMap(t => [
      { role: "user" as const, content: t.question },
      { role: "assistant" as const, content: t.answer },
    ]);
    const request = new AbortController();
    controller.current = request;
    setInput(""); setBusy(true); setError(""); setStatus("正在连接知识库");
    setTurns(old => [...old, { question, answer: "", sources: [], complete: false }]);
    const update = (apply: (turn: Turn) => Turn) => setTurns(old => old.map((t, i) => i === old.length - 1 ? apply(t) : t));
    try {
      await streamChat([...previous, { role: "user", content: question }], request.signal, event => {
        if (event.event === "status") setStatus(event.data.message);
        if (event.event === "sources") update(t => ({ ...t, sources: event.data }));
        if (event.event === "token") update(t => ({ ...t, answer: t.answer + event.data.text }));
        if (event.event === "done") update(t => ({ ...t, complete: true }));
      });
      setStatus("回答完成");
    } catch (e) {
      setError(request.signal.aborted ? "已停止，未完成的回答不会作为下一轮上下文。" : e instanceof Error ? e.message : "请求失败");
      setStatus("");
    } finally { controller.current = null; setBusy(false); }
  }
  return <div className="min-h-screen bg-stone-50 text-stone-800 md:grid md:grid-cols-[250px_1fr]">
    <aside className="border-r border-stone-200 bg-stone-100 p-6 md:sticky md:top-0 md:h-screen">
      <div className="text-xl font-semibold tracking-tight">静知 <span className="text-xs font-normal text-stone-500">STATIC / 01</span></div>
      <button className="mt-8 w-full rounded-xl border border-stone-300 bg-white p-3 text-left disabled:opacity-40" disabled={busy} onClick={() => { setTurns([]); setError(""); setStatus(""); }}>＋ 新的问答</button>
      <KnowledgePanel />
      <p className="mt-10 text-xs leading-6 text-stone-500">{health}</p>
    </aside>
    <main className="mx-auto flex min-h-screen w-full max-w-4xl flex-col px-5 md:px-12">
      <header className="border-b border-stone-200 py-6 text-sm text-stone-500">文档问答 <span className="float-right">基于来源，逐步求证</span></header>
      <section className="flex-1 py-8" aria-label="对话">
        {!turns.length && <div className="py-16"><p className="text-sm text-teal-700">你的知识，有据可循。</p><h1 className="mt-4 text-4xl font-semibold leading-tight">从一个好问题开始。</h1><p className="mt-5 leading-7 text-stone-500">搜索相关文档，阅读证据，再组织回答。</p><button className="mt-8 rounded-xl border border-stone-200 bg-white p-4 text-sm" onClick={() => setInput("南溪项目地基基础施工的计划完成日期是哪一天？")}>南溪项目地基基础施工何时完成？ ↗</button></div>}
        {turns.map((turn, i) => <article key={i} className="mb-10">
          <div className="mb-6 ml-auto w-fit max-w-full rounded-2xl bg-stone-200/70 px-5 py-3 whitespace-pre-wrap">{turn.question}</div>
          <div className="mb-3 text-xs font-semibold tracking-widest text-teal-700">静知</div>
          <div className="markdown"><Markdown remarkPlugins={[remarkGfm]}>{turn.answer || (busy && i === turns.length - 1 ? "正在查阅…" : "未生成回答")}</Markdown></div>
          {!!turn.sources.length && <details className="mt-5 rounded-xl border border-stone-200 bg-white p-4"><summary className="cursor-pointer text-sm">已读证据 · {turn.sources.length}</summary><div className="mt-3 grid gap-3">{turn.sources.map((s, j) => <div key={j}><a className="text-sm text-teal-800 underline" href={s.url.startsWith('/api/documents/') ? s.url : undefined} target="_blank" rel="noreferrer">[{j + 1}] {s.title} · 第{s.page ?? 1}页</a><p className="mt-1 text-xs text-stone-500">{s.snippet}</p></div>)}</div></details>}
        </article>)}
        <div role="status" className="text-sm text-teal-700">{status}</div>
        {error && <p role="alert" className="mt-3 text-sm text-red-700">{error}</p>}
        <div ref={bottom}/>
      </section>
      <form className="sticky bottom-0 bg-stone-50 pb-6 pt-3" onSubmit={e => { e.preventDefault(); void send(); }}>
        <div className="rounded-2xl border border-stone-300 bg-white p-3 shadow-sm">
          <textarea aria-label="问题" maxLength={12000} rows={3} className="w-full resize-none p-2 outline-none" placeholder="向文档提问…" value={input} onChange={e => setInput(e.target.value)}/>
          <div className="flex items-center justify-between"><span className="text-xs text-stone-400">当前对话仅保留在此页面</span>{busy ? <button type="button" className="rounded-lg bg-stone-800 px-5 py-2 text-sm text-white" onClick={() => controller.current?.abort()}>停止</button> : <button disabled={!input.trim()} className="rounded-lg bg-teal-800 px-5 py-2 text-sm text-white disabled:opacity-40">发送 ↑</button>}</div>
        </div>
      </form>
    </main>
  </div>;
}
createRoot(document.getElementById("root")!).render(<App/>);
