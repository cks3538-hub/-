import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  averageCost,
  unrealizedPnL,
  percentChange,
  riskLevel,
  netPnL,
  breakEvenPrice,
} from '../src/price-calc.js';
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

test('netPnL: feeRate 0이면 unrealizedPnL과 동일', () => {
  assert.equal(netPnL(trades, 400, 0), unrealizedPnL(trades, 400));
  assert.equal(netPnL(trades, 400), unrealizedPnL(trades, 400));
});

test('netPnL: feeRate 0.0005 적용 시 수수료 차감', () => {
  // notional = 1*100 + 1*200 + 0.5*300 = 450, fee = 450*0.0005 = 0.225
  assert.equal(netPnL(trades, 400, 0.0005), 375 - 0.225);
});

test('breakEvenPrice: feeRate 0이면 평균 매수가와 동일', () => {
  assert.equal(breakEvenPrice(trades, 0), averageCost(trades));
  assert.equal(breakEvenPrice(trades), averageCost(trades));
});

test('breakEvenPrice: feeRate 0.0005 적용 시 평균가보다 높음', () => {
  // avg 150 + fee(0.225) / held(1.5) = 150.15
  assert.equal(breakEvenPrice(trades, 0.0005), 150.15);
});

test('breakEvenPrice: 보유 수량이 0이면 0 반환', () => {
  const flatTrades = [
    { side: 'buy', qty: 1, price: 100 },
    { side: 'sell', qty: 1, price: 200 },
  ];
  assert.equal(breakEvenPrice(flatTrades, 0.0005), 0);
});

test('summarize: 포트폴리오 요약', () => {
  const [btc] = summarize([{ symbol: 'BTC', trades }], { BTC: 400 });
  assert.equal(btc.pnl, 375);
  assert.equal(btc.risk, 'high');
});
