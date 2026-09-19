import { test } from 'node:test';
import assert from 'node:assert/strict';
import { averageCost, unrealizedPnL, percentChange, riskLevel } from '../src/price-calc.js';
import { summarize } from '../src/portfolio.js';

const trades = [
  { side: 'buy', qty: 1, price: 100 },
  { side: 'buy', qty: 1, price: 200 },
  { side: 'sell', qty: 0.5, price: 300 },
];

test('averageCost: 매수 평균가 계산', () => {
  assert.equal(averageCost(trades), 150);
});

test('unrealizedPnL: 보유 1.5개, 현재가 400 → (400-150)*1.5', () => {
  assert.equal(unrealizedPnL(trades, 400), 375);
});

test('percentChange: 100 → 150 은 +50%', () => {
  assert.equal(percentChange(100, 150), 50);
  assert.throws(() => percentChange(0, 1), RangeError);
});

test('riskLevel: 등급 경계', () => {
  assert.equal(riskLevel(3), 'low');
  assert.equal(riskLevel(-10), 'medium');
  assert.equal(riskLevel(20), 'high');
});

test('summarize: 포트폴리오 요약', () => {
  const [btc] = summarize([{ symbol: 'BTC', trades }], { BTC: 400 });
  assert.equal(btc.pnl, 375);
  assert.equal(btc.risk, 'high');
});
