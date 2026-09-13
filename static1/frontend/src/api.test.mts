import { test } from 'node:test';
import assert from 'node:assert/strict';
import { streamChat } from './api.ts';

test('reassembles split UTF-8 and SSE frames', async () => {
  const original = globalThis.fetch;
  const bytes = new TextEncoder().encode('event: token\ndata: {"text":"证据"}\n\nevent: done\ndata: {"ok":true}\n\n');
  globalThis.fetch = async () => new Response(new ReadableStream({
    start(controller) { for (const byte of bytes) controller.enqueue(Uint8Array.of(byte)); controller.close(); },
  }));
  try {
    const events: unknown[] = [];
    await streamChat([{ role: 'user', content: 'hi' }], new AbortController().signal, event => events.push(event));
    assert.deepEqual(events, [{ event: 'token', data: { text: '证据' } }, { event: 'done', data: { ok: true } }]);
  } finally { globalThis.fetch = original; }
});

test('rejects incomplete responses and backend error events', async () => {
  const original = globalThis.fetch;
  try {
    for (const [body, expected] of [
      ['event: token\ndata: {"text":"partial"}\n\n', /连接中断/],
      ['event: error\ndata: {"message":"模型不可用"}\n\n', /模型不可用/],
    ] as const) {
      globalThis.fetch = async () => new Response(body);
      await assert.rejects(streamChat([], new AbortController().signal, () => {}), expected);
    }
  } finally { globalThis.fetch = original; }
});
