import { ScoreCard } from './ScoreCard';
import type { QualityMetrics } from '../types';

/**
 * QualityMetricsSection — exibe os cards de métricas de qualidade
 * organizados em três grupos: Eficiência, Qualidade e Resiliência.
 *
 * Requirements: 8.1, 8.3, 8.4
 */
export interface QualityMetricsSectionProps {
  qualityMetrics: QualityMetrics | null;
}

const EFFICIENCY_METRICS = [
  { key: 'E1' as const, label: 'Overhead de coordenação' },
  { key: 'E2' as const, label: 'Latency breakdown' },
  { key: 'E3' as const, label: 'Communication efficiency' },
];

const QUALITY_METRICS = [
  { key: 'Q1' as const, label: 'Consistência determinística' },
  { key: 'Q2' as const, label: 'Faithfulness' },
  { key: 'Q3' as const, label: 'Completeness' },
  { key: 'Q4' as const, label: 'Structural quality' },
];

const RESILIENCE_METRICS = [
  { key: 'R1' as const, label: 'Partial result coverage' },
  { key: 'R2' as const, label: 'Graceful degradation score' },
];

/**
 * O backend envia métricas organizadas por eixo, onde cada métrica é um dict complexo:
 *   { efficiency: { star: { coordination_overhead: {overhead_ratio: 0.3, ...}, ... } } }
 *
 * Extraímos o valor numérico principal de cada métrica para exibição nos ScoreCards.
 * Se a estrutura for irreconhecível, retorna null (não renderiza, não crasha).
 */
function extractNumber(val: unknown): number | null {
  if (val === null || val === undefined) return null;
  if (typeof val === 'number') return val;
  if (typeof val === 'object' && val !== null) {
    const obj = val as Record<string, unknown>;
    // Tentar extrair campos numéricos comuns
    if (typeof obj.score === 'number') return obj.score;
    if (typeof obj.overhead_ratio === 'number') return obj.overhead_ratio;
    if (typeof obj.overhead_percent === 'number') return obj.overhead_percent;
    if (typeof obj.messages_per_agent === 'number') return obj.messages_per_agent;
    if (typeof obj.total_ms === 'number') return obj.total_ms;
    if (typeof obj.coverage === 'number') return obj.coverage;
    if (typeof obj.all_identical === 'boolean') return obj.all_identical ? 1.0 : 0.0;
    // Fallback: primeiro valor numérico encontrado
    for (const v of Object.values(obj)) {
      if (typeof v === 'number') return v;
    }
  }
  return null;
}

function normalizeMetrics(raw: unknown): QualityMetrics | null {
  if (!raw || typeof raw !== 'object') return null;

  const data = raw as Record<string, unknown>;

  // Formato frontend: { star: { efficiency: { E1, E2, E3 }, ... }, hierarchical: {...} }
  if (data.star && data.hierarchical &&
      typeof data.star === 'object' && typeof data.hierarchical === 'object') {
    const star = data.star as Record<string, unknown>;
    const hier = data.hierarchical as Record<string, unknown>;
    if (star.efficiency && star.quality && star.resilience &&
        hier.efficiency && hier.quality && hier.resilience) {
      return raw as QualityMetrics;
    }
  }

  // Formato backend: { efficiency: { star: {...}, hierarchical: {...} }, quality: {...}, resilience: {...} }
  if (data.efficiency && data.quality && data.resilience) {
    const eff = data.efficiency as Record<string, unknown>;
    const qual = data.quality as Record<string, unknown>;
    const res = data.resilience as Record<string, unknown>;

    const starEff = eff.star as Record<string, unknown> | undefined;
    const hierEff = eff.hierarchical as Record<string, unknown> | undefined;
    const starQual = qual.star as Record<string, unknown> | undefined;
    const hierQual = qual.hierarchical as Record<string, unknown> | undefined;
    const starRes = res.star as Record<string, unknown> | undefined;
    const hierRes = res.hierarchical as Record<string, unknown> | undefined;

    if (!starEff || !hierEff) return null;

    return {
      star: {
        efficiency: {
          E1: extractNumber(starEff.coordination_overhead) ?? 0,
          E2: extractNumber(starEff.latency_breakdown) ?? 0,
          E3: extractNumber(starEff.communication_efficiency) ?? 0,
        },
        quality: {
          Q1: extractNumber(qual.deterministic_consistency) ?? 0,
          Q2: extractNumber(starQual?.faithfulness) ?? 0,
          Q3: extractNumber(starQual?.completeness) ?? 0,
          Q4: extractNumber(starQual?.structural_quality) ?? 0,
        },
        resilience: {
          R1: extractNumber(starRes?.partial_result_coverage) ?? 0,
          R2: extractNumber(starRes?.graceful_degradation) ?? 0,
        },
      },
      hierarchical: {
        efficiency: {
          E1: extractNumber(hierEff.coordination_overhead) ?? 0,
          E2: extractNumber(hierEff.latency_breakdown) ?? 0,
          E3: extractNumber(hierEff.communication_efficiency) ?? 0,
        },
        quality: {
          Q1: extractNumber(qual.deterministic_consistency) ?? 0,
          Q2: extractNumber(hierQual?.faithfulness) ?? 0,
          Q3: extractNumber(hierQual?.completeness) ?? 0,
          Q4: extractNumber(hierQual?.structural_quality) ?? 0,
        },
        resilience: {
          R1: extractNumber(hierRes?.partial_result_coverage) ?? 0,
          R2: extractNumber(hierRes?.graceful_degradation) ?? 0,
        },
      },
    };
  }

  return null;
}

export function QualityMetricsSection({ qualityMetrics }: QualityMetricsSectionProps): JSX.Element | null {
  const normalized = normalizeMetrics(qualityMetrics);
  if (!normalized) return null;

  const { star, hierarchical } = normalized;

  return (
    <div className="quality-metrics-section" data-testid="quality-metrics-section">
      <div className="quality-metrics-title">Métricas de Qualidade</div>

      {/* Eficiência */}
      <div className="score-cards-group">
        <div className="score-cards-group-title">Eficiência</div>
        <div className="score-cards-grid">
          {EFFICIENCY_METRICS.map(({ key, label }) => (
            <ScoreCard
              key={key}
              id={key}
              label={label}
              starValue={star.efficiency[key] ?? null}
              hierValue={hierarchical.efficiency[key] ?? null}
            />
          ))}
        </div>
      </div>

      {/* Qualidade */}
      <div className="score-cards-group">
        <div className="score-cards-group-title">Qualidade</div>
        <div className="score-cards-grid">
          {QUALITY_METRICS.map(({ key, label }) => (
            <ScoreCard
              key={key}
              id={key}
              label={label}
              starValue={star.quality[key] ?? null}
              hierValue={hierarchical.quality[key] ?? null}
            />
          ))}
        </div>
      </div>

      {/* Resiliência */}
      <div className="score-cards-group">
        <div className="score-cards-group-title">Resiliência</div>
        <div className="score-cards-grid">
          {RESILIENCE_METRICS.map(({ key, label }) => (
            <ScoreCard
              key={key}
              id={key}
              label={label}
              starValue={star.resilience[key] ?? null}
              hierValue={hierarchical.resilience[key] ?? null}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
