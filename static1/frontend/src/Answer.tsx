import { useEffect, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { formatDuration, type Attempt } from "./conversation";
import { stopLabels } from "./policy";

function Timing({ attempt, startedTick }: { attempt: Attempt; startedTick?: number }) {
  const [now, setNow] = useState(() => performance.now());
  const running = attempt.outcome === "running";
  useEffect(() => {
    if (!running) return;
    setNow(performance.now());
    const timer = setInterval(() => setNow(performance.now()), 100);
    return () => clearInterval(timer);
  }, [running, startedTick]);
  const elapsed = running && startedTick !== undefined ? Math.max(0, now - startedTick) : attempt.elapsedMs;
  const first = attempt.firstTokenMs === null
    ? running ? `等待中${elapsed === null ? "" : ` ${formatDuration(elapsed)}`}` : "未收到正文"
    : formatDuration(attempt.firstTokenMs);
  const usage = attempt.usage;
  const tokenText = usage?.complete && attempt.complete
    ? `${usage.total_tokens?.toLocaleString()}（输入 ${usage.input_tokens?.toLocaleString()} / 输出 ${usage.output_tokens?.toLocaleString()}）`
    : usage?.reported_tokens != null
      ? `已报告 ${usage.reported_tokens.toLocaleString()} · ${running ? "统计中" : "不完整"}`
      : running ? "统计中" : "未报告";
  return <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-xs text-stone-500" aria-label="回答耗时">
    <span title="从发送到收到第一段非空正文，包含检索、阅读、模型响应及网络等待；不是模型内部推理耗时。">首 token / Think 等待：{first}</span>
    {attempt.complete
      ? <span>总时间：{attempt.totalMs === null ? "未报告" : formatDuration(attempt.totalMs)}</span>
      : <span>{running ? "总时间" : "已耗时"}：{elapsed === null ? "未报告" : formatDuration(elapsed)} · {running ? "进行中" : attempt.outcome === "cancelled" ? "已停止（未完成）" : "失败（未完成）"}</span>}
    <span aria-label="本轮 Token 用量" title="本轮所有模型调用的输入和输出合计，包含意图判断、查证、研究及回答；重新生成单独统计。缺失数据不估算，历史回答无法补算。">本轮 Token：{tokenText}</span>
  </div>;
}

export function Answer({ attempt, startedTick }: { attempt: Attempt; startedTick?: number }) {
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");
  useEffect(() => { setCopyState("idle"); }, [attempt.answer]);
  useEffect(() => {
    if (copyState === "idle") return;
    const timer = setTimeout(() => setCopyState("idle"), 3000);
    return () => clearTimeout(timer);
  }, [copyState]);
  async function copy() {
    try {
      await navigator.clipboard.writeText(attempt.answer);
      setCopyState("copied");
    } catch {
      setCopyState("failed");
    }
  }
  return <>
    {attempt.policy && <p className="mb-2 text-xs text-stone-500" aria-label="生效策略">{{ direct: "直接交流", research: "资料研究", clarify: "需要澄清" }[attempt.policy.route]} · {{ low: "自由讨论", middle: "有据分析", high: "严格依据" }[attempt.policy.evidence_level]} · {attempt.policy.query_routing === "knowledge_only" ? "仅按资料" : "自动判断"} · {attempt.policy.allowed_doc_ids ? "限定资料" : attempt.policy.route === "direct" ? "未检索资料" : "全部资料"}<span className="ml-2">{stopLabels[attempt.policy.stop_reason] ?? "处理已更新"}</span></p>}
    <div className="markdown"><Markdown remarkPlugins={[remarkGfm]}>{attempt.answer || (attempt.outcome === "running" ? "正在回应…" : "未生成回答")}</Markdown></div>
    {attempt.telemetry && <details className="mt-3 text-xs text-stone-500"><summary>运行记录</summary><p>{{quick: "快速查证", quick_then_research: "快速查证后深入研究", research: "深入研究", direct: "直接交流"}[attempt.telemetry.path ?? ""] ?? "旧版本未记录路径"}</p><p>搜索 {attempt.telemetry.searches} 次 · 已读 {attempt.telemetry.reads} 段 · 用量已报告 {attempt.usage?.reported_calls ?? "未知"} / {attempt.usage?.calls ?? "未知"} 次模型调用</p>{Object.entries(attempt.telemetry.stages_ms).map(([stage, ms]) => <p key={stage}>{{understand: "理解问题", research: "查证", validate: "核验", answer: "组织回答", direct: "直接交流", finish: "完成"}[stage] ?? stage}：{formatDuration(ms)}</p>)}</details>}
    <Timing attempt={attempt} startedTick={startedTick}/>
    <div className="mt-3 flex flex-wrap items-center gap-3 text-xs">
      <button type="button" disabled={!attempt.answer || attempt.outcome === "running"} onClick={() => void copy()} className="rounded-lg border border-stone-300 px-3 py-1.5 text-stone-600 hover:bg-stone-100 disabled:opacity-40">{copyState === "copied" ? "已复制" : "复制答案"}</button>
      {copyState === "failed" && <span role="alert" className="text-red-700">复制失败，请手动选择答案复制。</span>}
      <span className="sr-only" role="status">{copyState === "copied" ? "答案已复制" : ""}</span>
    </div>
    {!!attempt.sources.length && <details className="mt-5 rounded-xl border border-stone-200 bg-white p-4"><summary className="cursor-pointer text-sm">已读证据 · {attempt.sources.length}</summary><div className="mt-3 grid gap-3">{attempt.sources.map((s, j) => <div key={j}><a className="text-sm text-teal-800 underline" href={s.url.startsWith('/api/documents/') ? s.url : undefined} target="_blank" rel="noreferrer">[{j + 1}] {s.title} · 第{s.page ?? 1}页</a><p className="mt-1 text-xs text-stone-500">{s.snippet}</p><p className="text-xs text-stone-500">版本 {s.version} · 采集 {s.captured_at ?? "未记录"}{s.truncated ? " · 仅部分原文，请继续阅读" : ""}</p></div>)}</div></details>}
    {attempt.sources.filter(source => source.origin?.startsWith("https://docs.langchain.com/")).map((source, index) => <a key={index} className="mr-4 text-xs text-teal-700 underline" href={source.origin} target="_blank" rel="noreferrer">官方原文 · {source.title}</a>)}
  </>;
}
