import { count } from "../format";
import type { AmenityImpact as Impact } from "../types/listing";
import { AMENITY_LABELS } from "../types/listing";

/**
 * What the square metre costs with each extra, next to what it costs without.
 *
 * The warning underneath is not boilerplate, it is the whole point of reading
 * this honestly: these are **correlations**. A pool does not add 7% to a flat;
 * flats with pools happen to sit where the metre already costs that. The one
 * case where the arrow probably does point the other way is the terrace, which
 * comes out *negative* — terraces are common in the cheaper outer
 * neighbourhoods, so "has a terrace" is partly a proxy for "is not central".
 */

interface AmenityImpactProps {
  impacts: Impact[];
}

export default function AmenityImpact({ impacts }: AmenityImpactProps) {
  if (impacts.length === 0) return null;

  // Symmetric scale around zero, so a +46% bar and a -12% bar are drawn on the
  // same ruler and can be compared by eye.
  const widest = Math.max(...impacts.map((item) => Math.abs(item.difference)), 1);

  return (
    <>
      <h3>Qué acompaña a un precio alto</h3>
      <ul className="impact">
        {impacts.map((item) => {
          const positive = item.difference >= 0;
          return (
            <li key={item.amenity}>
              <span className="impact__name">{AMENITY_LABELS[item.amenity]}</span>
              <span className="impact__track">
                <span
                  className={`impact__bar impact__bar--${positive ? "up" : "down"}`}
                  style={{ width: `${(Math.abs(item.difference) / widest) * 50}%` }}
                  title={
                    `Con: ${count(item.with_it)} €/m² · ` +
                    `sin: ${count(item.without_it)} €/m² · ` +
                    `lo tiene el ${item.share}% (${count(item.count)} anuncios)`
                  }
                />
              </span>
              <span className={`impact__value${positive ? "" : " impact__value--down"}`}>
                {positive ? "+" : ""}
                {Math.round(item.difference)} %
              </span>
            </li>
          );
        })}
      </ul>
      <p className="impact__warning muted">
        Diferencia de €/m² entre tenerlo y no tenerlo. Es una <strong>correlación</strong>: la
        piscina no sube el piso un 7 %, es que los pisos con piscina están donde el metro ya
        vale eso. La terraza sale negativa por lo mismo, al revés.
      </p>
    </>
  );
}
