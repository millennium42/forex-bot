import type { TradeRecord } from "@/lib/api";

export interface Insight {
  id: string;
  question: string;
  answer: string;
  tone: "good" | "bad" | "neutral";
}

const MIN_GROUP_SIZE = 3;

function winRate(trades: TradeRecord[]): number {
  return (trades.filter((t) => t.outcome?.was_correct).length / trades.length) * 100;
}

function avgPnl(trades: TradeRecord[]): number {
  return trades.reduce((sum, t) => sum + (t.outcome?.pnl ?? 0), 0) / trades.length;
}

function hourEffectInsight(closed: TradeRecord[]): Insight | null {
  const before = closed.filter((t) => t.closed_at && new Date(t.closed_at).getHours() < 16);
  const after = closed.filter((t) => t.closed_at && new Date(t.closed_at).getHours() >= 16);
  if (before.length < MIN_GROUP_SIZE || after.length < MIN_GROUP_SIZE) return null;

  const wrBefore = winRate(before);
  const wrAfter = winRate(after);
  const diff = wrAfter - wrBefore;
  if (Math.abs(diff) < 3) return null;

  return {
    id: "hour-effect",
    question: "O horário afeta seu win rate?",
    answer: `Seu win rate ${diff < 0 ? "cai" : "sobe"} ${Math.abs(diff).toFixed(0)} pontos depois das 16h (${wrBefore.toFixed(0)}% → ${wrAfter.toFixed(0)}%).`,
    tone: diff < 0 ? "bad" : "good",
  };
}

function sentimentAlignmentInsight(closed: TradeRecord[]): Insight | null {
  const withSentiment = closed.filter((t) => t.signal && t.signal.sentiment_score !== null);
  const aligned = withSentiment.filter(
    (t) => (t.signal!.sentiment_score! >= 0 ? "buy" : "sell") === t.side
  );
  const against = withSentiment.filter(
    (t) => (t.signal!.sentiment_score! >= 0 ? "buy" : "sell") !== t.side
  );
  if (aligned.length < MIN_GROUP_SIZE || against.length < MIN_GROUP_SIZE) return null;

  const avgAligned = avgPnl(aligned);
  const avgAgainst = avgPnl(against);
  if (avgAgainst >= 0 || avgAligned <= avgAgainst) return null;

  const factor = avgAligned !== 0 ? Math.abs(avgAgainst) / Math.abs(avgAligned) : null;

  return {
    id: "sentiment-alignment",
    question: "Vale a pena operar contra o sentimento?",
    answer: factor
      ? `Trades contra o sentimento perdem em média ${factor.toFixed(1)}x mais do que os alinhados com ele.`
      : "Trades contra o sentimento têm resultado pior do que os alinhados com ele.",
    tone: "bad",
  };
}

function weightVersionInsight(closed: TradeRecord[]): Insight | null {
  const byVersion = new Map<string, number[]>();
  for (const t of closed) {
    if (!t.signal || !t.outcome) continue;
    const arr = byVersion.get(t.signal.weight_version) ?? [];
    arr.push(t.outcome.pnl_pct);
    byVersion.set(t.signal.weight_version, arr);
  }
  const entries = Array.from(byVersion.entries()).filter(([, arr]) => arr.length >= MIN_GROUP_SIZE);
  if (entries.length < 2) return null;

  const averaged = entries
    .map(([version, arr]) => [version, arr.reduce((s, x) => s + x, 0) / arr.length] as const)
    .sort((a, b) => b[1] - a[1]);
  const [bestVersion, bestAvg] = averaged[0];
  const [worstVersion, worstAvg] = averaged[averaged.length - 1];
  if (bestAvg <= worstAvg) return null;

  const diffPct = worstAvg !== 0 ? Math.abs((bestAvg - worstAvg) / worstAvg) * 100 : 100;

  return {
    id: "weight-version",
    question: "Qual versão de pesos está performando melhor?",
    answer: `A versão de pesos ${bestVersion} está ${diffPct.toFixed(0)}% acima da ${worstVersion} em retorno médio por trade.`,
    tone: "good",
  };
}

export function computeInsights(trades: TradeRecord[]): Insight[] {
  const closed = trades.filter((t) => t.outcome !== null);
  const insights = [hourEffectInsight, sentimentAlignmentInsight, weightVersionInsight]
    .map((fn) => fn(closed))
    .filter((i): i is Insight => i !== null);

  if (insights.length === 0) {
    insights.push({
      id: "not-enough-data",
      question: "O que os dados mostram até agora?",
      answer: "Ainda não há trades encerrados suficientes para gerar insights confiáveis.",
      tone: "neutral",
    });
  }
  return insights;
}
