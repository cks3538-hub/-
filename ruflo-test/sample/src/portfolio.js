import { averageCost, unrealizedPnL, percentChange, riskLevel } from './price-calc.js';

/** 여러 코인의 손익을 한 번에 요약 */
export function summarize(positions, prices) {
  return positions.map(({ symbol, trades }) => {
    const price = prices[symbol];
    const avg = averageCost(trades);
    const pct = avg === 0 ? 0 : percentChange(avg, price);
    return {
      symbol,
      avg,
      price,
      pnl: unrealizedPnL(trades, price),
      pct,
      risk: riskLevel(pct),
    };
  });
}
