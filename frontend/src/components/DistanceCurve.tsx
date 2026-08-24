import { count } from "../format";
import type { Bucket } from "../types/listing";

/**
 * Price per square metre against distance to the city centre.
 *
 * Drawn as a line and not as bars because distance is continuous and ordered:
 * the shape between the points is the message — flat across the centre, then a
 * cliff — and bars invite reading each band as a separate category.
 *
 * It is only meaningful with **one city selected**, because the three cities
 * have different gradients; mixed together the curve averages three curves and
 * describes none of them. The caller decides whether to show it.
 */

interface DistanceCurveProps {
  buckets: Bucket[];
  /** Shown instead of the chart when several cities are mixed. */
  warning?: string;
}

const WIDTH = 300;
const HEIGHT = 90;
const PADDING = { top: 6, right: 4, bottom: 4, left: 4 };

export default function DistanceCurve({ buckets, warning }: DistanceCurveProps) {
  if (buckets.length < 3) return null;

  const values = buckets.map((bucket) => bucket.avg_price_per_m2);
  const top = Math.max(...values);
  // From zero, so the drop is drawn to the scale it really has: starting the
  // axis at the minimum would turn any gradient into a cliff.
  const scaleY = (value: number) =>
    PADDING.top + (1 - value / top) * (HEIGHT - PADDING.top - PADDING.bottom);
  const scaleX = (index: number) =>
    PADDING.left +
    (index / (buckets.length - 1)) * (WIDTH - PADDING.left - PADDING.right);

  const points = values.map((value, index) => `${scaleX(index)},${scaleY(value)}`);
  const area = `${PADDING.left},${HEIGHT} ${points.join(" ")} ${scaleX(buckets.length - 1)},${HEIGHT}`;

  return (
    <>
      <h3>Precio por m² según la distancia al centro</h3>
      {warning ? <p className="muted curve__warning">{warning}</p> : null}
      <svg
        className="curve"
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        preserveAspectRatio="none"
        role="img"
        aria-label={`Precio por metro cuadrado desde ${count(values[0])} a ${count(
          values[values.length - 1],
        )} euros según la distancia al centro`}
      >
        <polygon className="curve__area" points={area} />
        <polyline className="curve__line" points={points.join(" ")} />
        {buckets.map((bucket, index) => (
          <circle
            key={bucket.bucket}
            className="curve__dot"
            cx={scaleX(index)}
            cy={scaleY(bucket.avg_price_per_m2)}
            r={2.5}
          >
            <title>
              {`${bucket.label} km: ${count(bucket.avg_price_per_m2)} €/m² · ${count(
                bucket.count,
              )} anuncios`}
            </title>
          </circle>
        ))}
      </svg>
      <p className="curve__axis muted">
        {buckets.map((bucket) => (
          <span key={bucket.bucket}>{bucket.label}</span>
        ))}
      </p>
      <p className="curve__foot muted">
        km al centro · de {count(Math.min(...values))} a {count(top)} €/m²
      </p>
    </>
  );
}
