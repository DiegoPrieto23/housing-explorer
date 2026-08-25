import { useEffect, useState } from "react";

/** Cada cosa que hay que tener antes de que la primera vista signifique algo. */
export interface LoadingStep {
  label: string;
  /** `pending` mientras se pide, `done` cuando llega, `error` si no llega. */
  state: "pending" | "done" | "error";
}

interface LoadingScreenProps {
  steps: LoadingStep[];
}

/** Lo que dura el desvanecido. Tiene que coincidir con la transición del CSS. */
const FADE_MS = 400;

/**
 * La pantalla que tapa la aplicación mientras llegan los datos de la primera vista.
 *
 * Es una **capa por encima**, no una puerta antes de montar el mapa, y esa
 * distinción no es un atajo: la petición de anuncios necesita un bounding box,
 * el bounding box sale del viewport, y el viewport no existe hasta que Leaflet
 * se ha montado y ha medido su contenedor. Bloquear el renderizado hasta tener
 * los anuncios sería esperar a un dato que solo se puede pedir después de
 * renderizar. Así que el mapa se monta debajo, oculto, y esto se aparta cuando
 * ya hay algo que enseñar — que para quien mira es exactamente lo mismo.
 *
 * Enumera los pasos en vez de girar una rueda sin más porque los tres tardan
 * cosas distintas: los barrios son 279 kB, los puntos de interés 107, y los
 * anuncios dependen de lo que haya que agregar. Cuando uno se atasca, se ve
 * cuál.
 *
 * Un paso que falla no bloquea: la web se abre igual y lo que falta se dice en
 * su sitio. Una capa de barrios que no cargó no es motivo para no enseñar el
 * mapa.
 */
export default function LoadingScreen({ steps }: LoadingScreenProps) {
  const settled = steps.filter((step) => step.state !== "pending").length;
  const finished = settled === steps.length;

  // Se desmonta *después* de desvanecerse; si no, la capa desaparecería de
  // golpe y la transición no llegaría a verse.
  const [mounted, setMounted] = useState(true);

  useEffect(() => {
    if (!finished) return;
    const timer = window.setTimeout(() => setMounted(false), FADE_MS);
    return () => window.clearTimeout(timer);
  }, [finished]);

  if (!mounted) return null;

  const percent = steps.length === 0 ? 100 : Math.round((settled / steps.length) * 100);

  return (
    <div
      className={`splash${finished ? " splash--done" : ""}`}
      // aria-hidden en cuanto termina: durante el desvanecido sigue en el DOM,
      // y un lector de pantalla no debería anunciar lo que ya se está yendo.
      aria-hidden={finished}
      role="status"
      aria-live="polite"
    >
      <div className="splash__panel">
        <div className="splash__brand">
          <span className="splash__spinner" aria-hidden="true" />
          <div>
            <h1>Housing Explorer</h1>
            <p>149.923 anuncios de Madrid, Barcelona y Valencia</p>
          </div>
        </div>

        <ul className="splash__steps">
          {steps.map((step) => (
            <li key={step.label} className={`splash__step splash__step--${step.state}`}>
              <span className="splash__mark" aria-hidden="true">
                {step.state === "done" ? "✓" : step.state === "error" ? "!" : ""}
              </span>
              {step.label}
            </li>
          ))}
        </ul>

        <div className="splash__bar">
          <span className="splash__fill" style={{ width: `${percent}%` }} />
        </div>
      </div>
    </div>
  );
}
