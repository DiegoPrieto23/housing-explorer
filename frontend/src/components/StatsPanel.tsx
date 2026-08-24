import { count, euros, pricePerM2, shortEuros } from "../format";
import type { PriceBucket, Stats } from "../types/listing";
import AmenityImpact from "./AmenityImpact";
import DistanceCurve from "./DistanceCurve";
import Distribution from "./Distribution";

interface StatsPanelProps {
  stats: Stats | null;
  loading: boolean;
  error: string | null;
  /** Clicking a zone row filters by it. */
  onZoneSelect: (zone: string) => void;
  selectedZone: string | null;
}

/** Label for a histogram bar; the last bucket is open-ended. */
function bucketLabel(bucket: PriceBucket): string {
  return bucket.upper === null
    ? `${shortEuros(bucket.lower)} o más`
    : `${shortEuros(bucket.lower)} – ${shortEuros(bucket.upper)}`;
}

export default function StatsPanel({
  stats,
  loading,
  error,
  onZoneSelect,
  selectedZone,
}: StatsPanelProps) {
  if (error) {
    return (
      <section className="stats">
        <h2>Estadísticas</h2>
        <p className="notice notice--error">{error}</p>
      </section>
    );
  }

  if (!stats) {
    return (
      <section className="stats">
        <h2>Estadísticas</h2>
        <p className="muted">Calculando…</p>
      </section>
    );
  }

  const { overall, by_zone: byZone, price_distribution: distribution } = stats;
  // Relative to the tallest bar, so the shape is readable whatever the counts.
  const tallest = Math.max(1, ...distribution.map((bucket) => bucket.count));

  return (
    <section className={`stats${loading ? " is-loading" : ""}`}>
      <h2>Estadísticas del filtro</h2>

      {overall.count === 0 ? (
        <p className="muted">Sin anuncios que resumir.</p>
      ) : (
        <>
          <dl className="metrics">
            <div>
              <dt>Precio medio</dt>
              <dd>{euros(overall.avg_price)}</dd>
            </div>
            <div>
              <dt>Mediana</dt>
              <dd>{euros(overall.median_price)}</dd>
            </div>
            <div>
              <dt>Precio por m²</dt>
              <dd>{pricePerM2(overall.avg_price_per_m2)}</dd>
            </div>
            <div>
              <dt>Rango habitual</dt>
              <dd className="metrics__small">
                {shortEuros(overall.p25_price)} – {shortEuros(overall.p75_price)}
              </dd>
            </div>
          </dl>

          <h3>Distribución de precios</h3>
          <ul className="histogram">
            {distribution.map((bucket) => (
              <li key={bucket.lower} title={`${bucketLabel(bucket)}: ${count(bucket.count)}`}>
                <span
                  className="histogram__bar"
                  style={{ height: `${(bucket.count / tallest) * 100}%` }}
                />
              </li>
            ))}
          </ul>
          <p className="histogram__axis muted">
            <span>{shortEuros(overall.min_price)}</span>
            <span>
              p99 {shortEuros(overall.p99_price)}
              {overall.max_price !== overall.p99_price ? " +" : ""}
            </span>
          </p>

          <Distribution byRooms={stats.by_rooms} bySize={stats.by_size} />

          <DistanceCurve
            buckets={stats.by_distance}
            warning={
              // Madrid, Barcelona and Valencia have different gradients, and
              // "the centre" is a different place in each. Averaged together
              // the curve describes none of them, so say that rather than let
              // it be read as one city's.
              selectedZone === null && byZone.length > 1
                ? "Con varias ciudades mezcladas la curva promedia gradientes distintos. Elige una ciudad para leerla."
                : undefined
            }
          />

          <AmenityImpact impacts={stats.amenities} />

          <h3>Precio medio por zona</h3>
          <table className="zones">
            <thead>
              <tr>
                <th scope="col">Zona</th>
                <th scope="col">Anuncios</th>
                <th scope="col">Media</th>
                <th scope="col">€/m²</th>
              </tr>
            </thead>
            <tbody>
              {byZone.map((zone) => (
                <tr
                  key={zone.zone}
                  className={zone.zone === selectedZone ? "is-active" : ""}
                  onClick={() => onZoneSelect(zone.zone)}
                  title={`Filtrar por ${zone.zone}`}
                >
                  <th scope="row">{zone.zone}</th>
                  <td>{count(zone.count)}</td>
                  <td>{shortEuros(zone.avg_price)}</td>
                  <td>
                    {zone.avg_price_per_m2 === null
                      ? "—"
                      : pricePerM2(zone.avg_price_per_m2)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </section>
  );
}
