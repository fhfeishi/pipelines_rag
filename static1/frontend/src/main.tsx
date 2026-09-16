import { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { streamChat } from "./api";
import { Answer } from "./Answer";
import { newTurn, regenerateTurn, receiveEvent, stopTurn, type Turn } from "./conversation";
import "./style.css";
import { KnowledgePanel } from "./KnowledgePanel";
import { OfficialDocs } from "./OfficialDocs";

function App() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [health, setHealth] = useState("正在连接");
  const [ready, setReady] = useState(false);
  const [progress, setProgress] = useState<{ stage: string; completed: number; total: number } | null>(null);
  const controller = useRef<AbortController | null>(null);
  const startedTick = useRef(0);
  const bottom = useRef<HTMLDivElement>(null);
  function exportChat() {
    const blob = new Blob([JSON.stringify({ exported_at: new Date().toISOString(), service: health, turns }, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url; link.download = "static1-comparison.json"; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  useEffect(() => {
    const refresh = () => fetch("/api/health", { signal: AbortSignal.timeout(5000) }).then(r => { if (!r.ok) throw new Error(); return r.json(); })
      .then(data => {
        setReady(data.preparation === "ready" && data.api_key_configured);
        setProgress(data.index_progress ?? null);
        setHealth(data.preparation === "running" ? "知识库正在加载中" : data.preparation === "error" ? "知识库加载失败，请检查终端日志后重启" : data.api_key_configured ? "服务已连接 · " + data.model : "请在 .env 配置模型密钥后重启");
      })
      .catch(() => { setReady(false); setHealth("服务暂时未连接，正在自动重连…"); });
    void refresh();
    const timer = setInterval(refresh, 3000);
    return () => { clearInterval(timer); controller.current?.abort(); };
  }, []);
  useEffect(() => { bottom.current?.scrollIntoView({ behavior: "smooth" }); }, [turns, status]);
  async function send(regenerate = false) {
    const question = regenerate ? turns.at(-1)?.question : input.trim();
    if (!question || controller.current || !ready) return;
    const turn = regenerate ? regenerateTurn(turns[turns.length - 1], turns.slice(0, -1)) : newTurn(question, turns);
    const request = new AbortController();
    controller.current = request;
    const start = performance.now();
    startedTick.current = start;
    if (!regenerate) setInput("");
    setBusy(true); setError(""); setStatus("正在连接知识库");
    setTurns(old => regenerate ? [...old.slice(0, -1), turn] : [...old, turn]);
    const update = (apply: (turn: Turn) => Turn) => setTurns(old => old.map((t, i) => i === old.length - 1 ? apply(t) : t));
    try {
      await streamChat(turn.requestMessages, request.signal, event => {
        if (event.event === "status") setStatus(event.data.message);
        const elapsedMs = performance.now() - start;
        update(t => receiveEvent(t, event, elapsedMs));
      });
      setStatus("回答完成");
    } catch (e) {
      const elapsedMs = performance.now() - start;
      update(t => stopTurn(t, request.signal.aborted, elapsedMs));
      setError(request.signal.aborted ? "已停止，未完成的回答不会作为下一轮上下文。" : e instanceof Error ? e.message : "请求失败");
      setStatus("");
    } finally { controller.current = null; setBusy(false); }
  }
  return <div className="min-h-screen bg-stone-50 text-stone-800 md:grid md:grid-cols-[250px_1fr]">
    <aside className="border-r border-stone-200 bg-stone-100 p-6 md:sticky md:top-0 md:h-screen">
      <div className="text-xl font-semibold tracking-tight">静知 <span className="text-xs font-normal text-stone-500">STATIC / 01</span></div>
      <button className="mt-8 w-full rounded-xl border border-stone-300 bg-white p-3 text-left disabled:opacity-40" disabled={busy} onClick={() => { setTurns([]); setError(""); setStatus(""); }}>＋ 新的问答</button>
      <KnowledgePanel />
      <OfficialDocs />
      <button disabled={busy || !turns.length} className="mt-4 text-sm underline disabled:opacity-40" onClick={exportChat}>导出对话与证据版本</button>
      <p className="mt-10 text-xs leading-6 text-stone-500">{health}</p>
    </aside>
    <main className="mx-auto flex min-h-screen w-full max-w-4xl flex-col px-5 md:px-12">
      <header className="border-b border-stone-200 py-6 text-sm text-stone-500">文档问答 <span className="float-right">基于来源，逐步求证</span></header>
      {!ready && <section role="status" aria-live="polite" className="mt-6 rounded-2xl border border-teal-200 bg-teal-50 p-6">
        <h2 className="text-lg font-semibold text-teal-900">{health}</h2>
        <p className="mt-2 text-sm text-teal-800">可以先输入问题，准备完成后发送按钮会自动启用，无需刷新页面。</p>
        {progress?.stage === "loading_model" && <p className="mt-3 text-sm">正在加载本地 embedding 模型，首次使用需要一些时间。</p>}
        {progress && progress.total > 0 && <div className="mt-3"><progress className="w-full" value={progress.completed} max={progress.total}/><p className="text-xs">向量索引：{progress.completed} / {progress.total} 个片段</p></div>}
      </section>}
      <section className="flex-1 py-8" aria-label="对话">
        {!turns.length && <div className="py-16"><p className="text-sm text-teal-700">你的知识，有据可循。</p><h1 className="mt-4 text-4xl font-semibold leading-tight">从一个好问题开始。</h1><p className="mt-5 leading-7 text-stone-500">搜索相关文档，阅读证据，再组织回答。</p><button className="mt-8 rounded-xl border border-stone-200 bg-white p-4 text-sm" onClick={() => setInput("南溪项目地基基础施工的计划完成日期是哪一天？")}>南溪项目地基基础施工何时完成？ ↗</button></div>}
        {turns.map((turn, i) => <article key={i} className="mb-10">
          <div className="mb-6 ml-auto w-fit max-w-full rounded-2xl bg-stone-200/70 px-5 py-3 whitespace-pre-wrap">{turn.question}</div>
          <div className="mb-3 text-xs font-semibold tracking-widest text-teal-700">静知</div>
          <Answer attempt={turn} startedTick={i === turns.length - 1 ? startedTick.current : undefined}/>
          {i === turns.length - 1 && <button type="button" disabled={busy || !ready} className="mt-3 rounded-lg border border-stone-300 px-3 py-1.5 text-xs text-stone-600 hover:bg-stone-100 disabled:opacity-40" onClick={() => void send(true)}>重新生成</button>}
          {!!turn.previousAttempts.length && <details className="mt-4 rounded-xl border border-stone-200 p-4">
            <summary className="cursor-pointer text-sm text-stone-500">之前的回答 · {turn.previousAttempts.length} 个版本</summary>
            {turn.previousAttempts.map((attempt, version) => <div key={version} className="mt-4 border-t border-stone-200 pt-4"><p className="mb-3 text-xs text-stone-500">版本 {version + 1}</p><Answer attempt={attempt}/></div>)}
          </details>}
        </article>)}
        <div role="status" className="text-sm text-teal-700">{status}</div>
        {error && <p role="alert" className="mt-3 text-sm text-red-700">{error}</p>}
        <div ref={bottom}/>
      </section>
      <form className="sticky bottom-0 bg-stone-50 pb-6 pt-3" onSubmit={e => { e.preventDefault(); void send(); }}>
        <div className="rounded-2xl border border-stone-300 bg-white p-3 shadow-sm">
          <textarea aria-label="问题" maxLength={12000} rows={3} className="w-full resize-none p-2 outline-none" placeholder="向文档提问…" value={input} onChange={e => setInput(e.target.value)}/>
          <div className="flex items-center justify-between"><span className="text-xs text-stone-400">当前对话仅保留在此页面</span>{busy ? <button type="button" className="rounded-lg bg-stone-800 px-5 py-2 text-sm text-white" onClick={e => { e.preventDefault(); controller.current?.abort(); }}>停止</button> : <button type="submit" disabled={!input.trim() || !ready} className="rounded-lg bg-teal-800 px-5 py-2 text-sm text-white disabled:opacity-40">{ready ? "发送 ↑" : "等待知识库就绪"}</button>}</div>
        </div>
      </form>
    </main>
  </div>;
}
createRoot(document.getElementById("root")!).render(<App/>);
