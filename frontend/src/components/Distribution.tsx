import { useState } from "react";

import { count, shortEuros } from "../format";
import type { Bucket } from "../types/listing";

/**
 * The two distributions the notebook found most worth showing, as bar charts
 * narrow enough for a sidebar.
 *
 * Both plot **price per m²**, not total price, and that is the point of them.
 * Total price rises with size — which nobody needs a chart to learn. What the
 * analysis actually turned up is that the *unit* price does not: it dips in the
 * middle and rises at both ends, because studios are small **and** central while
 * large homes are in expensive neighbourhoods. That U is invisible on a chart
 * of totals.
 */

interface DistributionProps {
  byRooms: Bucket[];
  bySize: Bucket[];
}

type Dimension = "rooms" | "size";

const LABELS: Record<Dimension, { tab: string; axis: string; empty: string }> = {
  rooms: {
    tab: "Habitaciones",
    axis: "habitaciones",
    empty: "Ningún anuncio declara habitaciones y superficie a la vez.",
  },
  size: {
    tab: "Superficie",
    axis: "m² construidos",
    empty: "Ningún anuncio declara superficie.",
  },
};

export default function Distribution({ byRooms, bySize }: DistributionProps) {
  const [dimension, setDimension] = useState<Dimension>("rooms");
  const buckets = dimension === "rooms" ? byRooms : bySize;
  const labels = LABELS[dimension];

  // Scaled from zero, not from the smallest bar. Starting the axis at the
  // minimum would turn a 15% spread into a chart that looks like a cliff.
  const tallest = Math.max(1, ...buckets.map((bucket) => bucket.avg_price_per_m2));
  const total = buckets.reduce((sum, bucket) => sum + bucket.count, 0);

  return (
    <>
      <h3>Precio por m²</h3>
      <div className="segmented segmented--small" role="tablist" aria-label="Desglose">
        {(Object.keys(LABELS) as Dimension[]).map((key) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={dimension === key}
            className={dimension === key ? "is-active" : ""}
            onClick={() => setDimension(key)}
          >
            {LABELS[key].tab}
          </button>
        ))}
      </div>

      {buckets.length === 0 ? (
        <p className="muted">{labels.empty}</p>
      ) : (
        <>
          <ul className="bars">
            {buckets.map((bucket) => (
              <li key={bucket.bucket}>
                <span
                  className="bars__bar"
                  style={{ height: `${(bucket.avg_price_per_m2 / tallest) * 100}%` }}
                  title={
                    `${bucket.label} ${labels.axis}: ` +
                    `${count(bucket.avg_price_per_m2)} €/m² · ` +
                    `${count(bucket.count)} anuncios · media ${shortEuros(bucket.avg_price)}`
                  }
                />
                <span className="bars__label">{bucket.label}</span>
              </li>
            ))}
          </ul>
          <p className="bars__foot muted">
            {labels.axis} · hasta {count(tallest)} €/m²
            <span> · {count(total)} anuncios con superficie</span>
          </p>
        </>
      )}
    </>
  );
}
