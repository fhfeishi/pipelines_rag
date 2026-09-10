const HISTORY_KEY = "static1-chat-history";
const chatArea = document.getElementById("chat-area");
const welcome = document.getElementById("welcome");
const composer = document.getElementById("composer");
const input = document.getElementById("message-input");
const sendButton = document.getElementById("send-button");
const statusText = document.getElementById("status-text");
const statusDot = document.getElementById("status-dot");
const modelLabel = document.getElementById("model-label");
let messages = loadHistory();
let busy = false;

function loadHistory() {
  try { return JSON.parse(localStorage.getItem(HISTORY_KEY) || "[]"); }
  catch { return []; }
}

function saveHistory() {
  localStorage.setItem(HISTORY_KEY, JSON.stringify(messages.slice(-20)));
}

function escapeHtml(value) {
  return value.replace(/[&<>"']/g, (char) => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", "\"":"&quot;", "'":"&#039;"}[char]));
}

function renderMarkdown(value) {
  const blocks = [];
  let text = escapeHtml(value).replace(/```([\s\S]*?)```/g, (_, code) => {
    blocks.push(`<pre><code>${code.trim()}</code></pre>`);
    return `@@CODE${blocks.length - 1}@@`;
  });
  text = text.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>');
  text = text.replace(/`([^`]+)`/g, '<code>$1</code>').replace(/\n/g, "<br>");
  return text.replace(/@@CODE(\d+)@@/g, (_, index) => blocks[Number(index)]);
}

function appendMessage(role, content, streaming = false) {
  welcome?.remove();
  const wrapper = document.createElement("div");
  wrapper.className = `message ${role}`;
  wrapper.innerHTML = `<div class="avatar">${role === "user" ? "你" : "L"}</div><div class="bubble">${streaming ? "" : renderMarkdown(content)}</div>`;
  chatArea.appendChild(wrapper);
  chatArea.scrollTop = chatArea.scrollHeight;
  return wrapper.querySelector(".bubble");
}

function appendSources(sources) {
  if (!sources.length) return;
  const section = document.createElement("div");
  section.className = "sources";
  section.innerHTML = sources.map((source) => `<div class="source-card"><a href="${source.url}" target="_blank" rel="noreferrer">${escapeHtml(source.title)}</a><p>${escapeHtml(source.snippet || "官方文档")}</p></div>`).join("");
  chatArea.appendChild(section);
}

async function checkHealth() {
  try {
    const response = await fetch("/api/health");
    const data = await response.json();
    statusText.textContent = data.api_key_configured ? "服务已就绪" : "等待配置 API Key";
    statusDot.classList.toggle("offline", !data.api_key_configured);
    modelLabel.textContent = data.model || "DeepSeek";
  } catch {
    statusText.textContent = "后端未连接";
    statusDot.classList.add("offline");
  }
}

async function sendMessage(content) {
  if (!content.trim() || busy) return;
  busy = true;
  sendButton.disabled = true;
  input.value = "";
  input.style.height = "auto";
  messages.push({ role: "user", content });
  appendMessage("user", content);
  const assistantBubble = appendMessage("assistant", "", true);
  let answer = "";

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages }),
    });
    if (!response.ok) throw new Error((await response.json()).detail || "请求失败");
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const events = buffer.split("\n\n");
      buffer = events.pop();
      for (const raw of events) {
        const eventName = raw.match(/^event: (.+)$/m)?.[1];
        const payload = raw.match(/^data: (.+)$/m)?.[1];
        if (!payload) continue;
        const data = JSON.parse(payload);
        if (eventName === "sources") appendSources(data);
        if (eventName === "token") { answer += data.text; assistantBubble.innerHTML = renderMarkdown(answer); chatArea.scrollTop = chatArea.scrollHeight; }
        if (eventName === "error") { answer = data.message; assistantBubble.innerHTML = renderMarkdown(answer); }
      }
    }
    messages.push({ role: "assistant", content: answer });
    saveHistory();
  } catch (error) {
    assistantBubble.innerHTML = renderMarkdown(error.message || "请求失败，请检查后端日志。");
  } finally {
    busy = false;
    sendButton.disabled = false;
    input.focus();
  }
}

composer.addEventListener("submit", (event) => { event.preventDefault(); sendMessage(input.value); });
input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); composer.requestSubmit(); }
});
input.addEventListener("input", () => { input.style.height = "auto"; input.style.height = `${Math.min(input.scrollHeight, 180)}px`; });
document.querySelectorAll("[data-prompt]").forEach((button) => button.addEventListener("click", () => sendMessage(button.dataset.prompt)));
document.getElementById("new-chat").addEventListener("click", () => { messages = []; saveHistory(); location.reload(); });
document.getElementById("clear-chat").addEventListener("click", () => { messages = []; saveHistory(); location.reload(); });
document.getElementById("refresh-docs").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  button.textContent = "更新中…";
  try { await fetch("/api/docs/refresh", { method: "POST" }); button.textContent = "已更新"; }
  catch { button.textContent = "更新失败"; }
  finally { setTimeout(() => { button.disabled = false; button.textContent = "更新文档"; }, 1800); }
});

if (messages.length) {
  welcome.remove();
  messages.forEach((message) => appendMessage(message.role, message.content));
}
checkHealth();
