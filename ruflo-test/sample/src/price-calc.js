/**
 * 간단한 코인 매매 손익 계산 유틸리티.
 * ruflo 코드 분석(analyze) 명령의 대상이 되는 샘플 코드입니다.
 */

/** 평균 매수 단가: 총 매수금액 / 총 수량 */
export function averageCost(trades) {
  const buys = trades.filter((t) => t.side === 'buy');
  const qty = buys.reduce((s, t) => s + t.qty, 0);
  if (qty === 0) return 0;
  const cost = buys.reduce((s, t) => s + t.qty * t.price, 0);
  return cost / qty;
}

/** 현재가 기준 평가 손익 (금액) */
export function unrealizedPnL(trades, currentPrice) {
  const avg = averageCost(trades);
  const held = trades.reduce(
    (s, t) => (t.side === 'buy' ? s + t.qty : s - t.qty),
    0,
  );
  return (currentPrice - avg) * held;
}

/** 두 값 사이의 퍼센트 변화 */
export function percentChange(from, to) {
  if (from === 0) throw new RangeError('from must not be zero');
  return ((to - from) / from) * 100;
}

/** 리스크 등급: 변동률 크기에 따라 low / medium / high */
export function riskLevel(pct) {
  const abs = Math.abs(pct);
  if (abs < 5) return 'low';
  if (abs < 15) return 'medium';
  return 'high';
}
