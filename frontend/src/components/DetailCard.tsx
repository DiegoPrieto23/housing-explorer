import { useCallback } from "react";

import { fetchListing } from "../api";
import { deviation, euros, priceWithPeriod, rooms, squareMetres, unitPrice } from "../format";
import { useResource } from "../hooks/useResource";
import { markerColor, markerLabel } from "../markers";
import { placeholderImage } from "../placeholder";
import type { PropertyType } from "../types/listing";
import { isBargain } from "../types/listing";

interface DetailCardProps {
  /** `fuente:id` of the listing to show, or null to show nothing. */
  globalId: string | null;
  onClose: () => void;
  favourite: boolean;
  onToggleFavourite: (globalId: string) => void;
}

/**
 * The detail panel, pinned to the bottom-right corner.
 *
 * It replaces the Leaflet popup that used to open over the marker. A popup
 * covers exactly the part of the map you just clicked on, moves the viewport to
 * fit itself, and disappears when you pan — all of which fight the act of
 * comparing one property against its neighbours. A fixed panel stays put, and
 * the marker underneath stays visible.
 *
 * The map only ever sends the handful of fields a pin needs, so this is where
 * the full listing is fetched — one request, and only for what is clicked.
 */
export default function DetailCard({
  globalId,
  onClose,
  favourite,
  onToggleFavourite,
}: DetailCardProps) {
  const listing = useResource(
    `detail:${globalId ?? ""}`,
    useCallback(
      (signal: AbortSignal) => fetchListing(globalId as string, signal),
      [globalId],
    ),
    { enabled: globalId !== null },
  );

  if (globalId === null) return null;

  const data = listing.data;
  const accent = data ? markerColor(data.property_type as PropertyType) : undefined;

  return (
    <aside className="detail" aria-live="polite">
      <button
        type="button"
        className="detail__close"
        onClick={onClose}
        aria-label="Cerrar el detalle"
      >
        ×
      </button>

      {listing.error ? (
        <p className="notice notice--error">{listing.error}</p>
      ) : !data ? (
        <p className="muted">Cargando el anuncio…</p>
      ) : (
        <>
          <div className="detail__media">
            <img src={placeholderImage(data)} alt="" width={320} height={160} />
            {/*
              La etiqueta llevaba el color del tipo como fondo, y encima el
              texto en el color de texto normal: azul oscuro sobre gris oscuro,
              ilegible. Ahora es una ficha neutra con una muestra del color al
              lado — la misma que usa la leyenda del mapa para decir lo mismo.
            */}
            <span className="detail__badge">
              <span className="legend__swatch" style={{ background: accent }} />
              {markerLabel(data.property_type as PropertyType)}
            </span>
            <button
              type="button"
              className={`fav fav--detail${favourite ? " fav--on" : ""}`}
              aria-pressed={favourite}
              aria-label={favourite ? "Quitar de favoritos" : "Guardar en favoritos"}
              onClick={() => onToggleFavourite(globalId)}
            >
              {favourite ? "♥" : "♡"}
            </button>
          </div>

          <div className="detail__body">
            <p className="detail__price">{priceWithPeriod(data.price, data.operation)}</p>
            {unitPrice(data) ? <p className="muted">{unitPrice(data)}</p> : null}

            <h2 className="detail__title" title={data.title}>
              {data.title}
            </h2>

            <dl className="detail__facts">
              <div>
                <dt>Superficie</dt>
                <dd>{squareMetres(data.size_m2)}</dd>
              </div>
              <div>
                <dt>Habitaciones</dt>
                <dd>{rooms(data.rooms)}</dd>
              </div>
              <div>
                <dt>Operación</dt>
                <dd>{data.operation === "venta" ? "Venta" : "Alquiler"}</dd>
              </div>
              <div>
                <dt>Precio</dt>
                <dd>{euros(data.price)}</dd>
              </div>
            </dl>

            {data.expected_price !== null ? (
              <div className={`estimate${isBargain(data) ? " estimate--bargain" : ""}`}>
                <p className="estimate__head">
                  {isBargain(data) ? "★ Posible chollo" : "Estimación del modelo"}
                  <strong>{deviation(data.price_deviation)}</strong>
                </p>
                <p className="estimate__body">
                  El modelo estima <strong>{euros(data.expected_price)}</strong> para una vivienda
                  así en esta zona.
                </p>
                <p className="estimate__warning">
                  Es una señal estadística, no una tasación: el modelo se equivoca un 14 % de media
                  y no ve el estado real del inmueble. Un precio muy por debajo suele tener un
                  motivo.
                </p>
              </div>
            ) : null}

            <p className="detail__zone muted">
              {data.address ?? data.neighbourhood ?? data.zone ?? "Sin zona"}
              {data.neighbourhood && data.zone ? `, ${data.zone}` : ""}
            </p>

            {data.url ? (
              <a className="button" href={data.url} target="_blank" rel="noreferrer">
                Ver anuncio original
              </a>
            ) : (
              <p className="detail__id muted">{globalId}</p>
            )}
          </div>
        </>
      )}
    </aside>
  );
}
