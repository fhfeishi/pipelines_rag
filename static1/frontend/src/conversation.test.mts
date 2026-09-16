import { test } from 'node:test';
import assert from 'node:assert/strict';
import { newTurn, receiveEvent, regenerateTurn, stopTurn } from './conversation.ts';

test('first token ignores status, sources and empty chunks; completion freezes elapsed time', () => {
  let turn = newTurn('问题', []);
  turn = receiveEvent(turn, { event: 'status', data: { message: '正在研究' } }, 100);
  turn = receiveEvent(turn, { event: 'sources', data: [] }, 200);
  turn = receiveEvent(turn, { event: 'token', data: { text: '' } }, 300);
  assert.equal(turn.firstTokenMs, null);
  turn = receiveEvent(turn, { event: 'token', data: { text: '正文' } }, 1200);
  turn = receiveEvent(turn, { event: 'token', data: { text: '继续' } }, 1600);
  turn = receiveEvent(turn, { event: 'done', data: { ok: true } }, 2000);
  assert.equal(turn.firstTokenMs, 1200);
  assert.equal(turn.totalMs, 2000);
  assert.equal(turn.complete, true);
  assert.deepEqual(receiveEvent(turn, { event: 'done', data: { ok: true } }, 9000), turn);
  assert.deepEqual(stopTurn(turn, true, 10000), turn);
});

test('cancelled and failed attempts retain partial text without successful total or invented first token', () => {
  const empty = stopTurn(newTurn('问题', []), true, 500);
  assert.equal(empty.firstTokenMs, null);
  assert.equal(empty.totalMs, null);
  assert.equal(empty.elapsedMs, 500);
  let partial = receiveEvent(newTurn('问题', []), { event: 'token', data: { text: '部分' } }, 400);
  partial = stopTurn(partial, false, 800);
  assert.equal(partial.answer, '部分');
  assert.equal(partial.firstTokenMs, 400);
  assert.equal(partial.totalMs, null);
  assert.equal(partial.outcome, 'failed');
  assert.equal(partial.complete, false);
});

test('regeneration reuses original request, archives attempts and resets timers', () => {
  let first = newTurn('第一问', []);
  first = receiveEvent(first, { event: 'token', data: { text: '第一答' } }, 10);
  first = receiveEvent(first, { event: 'done', data: { ok: true } }, 20);
  let second = newTurn('追问', [first]);
  second = receiveEvent(second, { event: 'token', data: { text: '旧答案' } }, 30);
  second = receiveEvent(second, { event: 'done', data: { ok: true } }, 40);
  second.requestMessages.push({ role: 'assistant', content: '污染的旧快照' });
  second.sources = [{ title: '旧来源', url: '/old', snippet: '不能进入后端' }];
  const retry = regenerateTurn(second, [first]);
  assert.deepEqual(retry.requestMessages, [
    { role: 'user', content: '第一问' }, { role: 'assistant', content: '第一答' }, { role: 'user', content: '追问' },
  ]);
  assert.equal(retry.previousAttempts[0].answer, '旧答案');
  assert.equal(retry.previousAttempts[0].totalMs, 40);
  assert.equal(retry.firstTokenMs, null);
  assert.equal(retry.totalMs, null);
  assert.equal(retry.answer, '');
  assert.equal(JSON.stringify(retry.requestMessages).includes('旧'), false);
  assert.equal(JSON.stringify(retry.requestMessages).includes('snippet'), false);
  assert.deepEqual(newTurn('下一问', [first, stopTurn(retry, true, 90)]).requestMessages, [
    { role: 'user', content: '第一问' }, { role: 'assistant', content: '第一答' }, { role: 'user', content: '下一问' },
  ]);
});
