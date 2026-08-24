import { deviation, euros, priceWithPeriod, rooms, squareMetres, unitPrice } from "../format";
import { placeholderImage } from "../placeholder";
import type { Listing } from "../types/listing";
import { globalId, isBargain } from "../types/listing";

interface ListingCardProps {
  listing: Listing;
  /** Highlighted because it is the listing selected on the map. */
  active?: boolean;
  onSelect?: (listing: Listing) => void;
  favourite?: boolean;
  onToggleFavourite?: (globalId: string) => void;
}

export default function ListingCard({
  listing,
  active,
  onSelect,
  favourite = false,
  onToggleFavourite,
}: ListingCardProps) {
  const perM2 = unitPrice(listing);
  const bargain = isBargain(listing);
  const id = globalId(listing);

  return (
    <article className={`card${active ? " card--active" : ""}${bargain ? " card--bargain" : ""}`}>
      <div className="card__media">
        <img src={placeholderImage(listing)} alt="" loading="lazy" width={200} height={150} />
        <span className="card__badge">{listing.operation === "venta" ? "Venta" : "Alquiler"}</span>
        {onToggleFavourite ? (
          <button
            type="button"
            className={`fav${favourite ? " fav--on" : ""}`}
            aria-pressed={favourite}
            aria-label={favourite ? "Quitar de favoritos" : "Guardar en favoritos"}
            title={favourite ? "Quitar de favoritos" : "Guardar en favoritos"}
            onClick={() => onToggleFavourite(id)}
          >
            {favourite ? "♥" : "♡"}
          </button>
        ) : null}
        {bargain ? (
          <span
            className="card__deal"
            title={`El modelo estima ${euros(listing.expected_price)} para esta vivienda`}
          >
            ★ Posible chollo {deviation(listing.price_deviation)}
          </span>
        ) : null}
      </div>

      <div className="card__body">
        <p className="card__price">{priceWithPeriod(listing.price, listing.operation)}</p>
        {perM2 ? <p className="card__unit muted">{perM2}</p> : null}

        <h3 className="card__title" title={listing.title}>
          {listing.title}
        </h3>

        <p className="card__facts">
          <span>{squareMetres(listing.size_m2)}</span>
          <span>{rooms(listing.rooms)}</span>
          <span className="card__type">{listing.property_type}</span>
        </p>

        <p className="card__zone muted">{listing.address ?? listing.zone ?? "Sin zona"}</p>

        {listing.expected_price !== null && !bargain ? (
          <p className="card__estimate muted" title="Precio que estima el modelo">
            estimado {euros(listing.expected_price)} · {deviation(listing.price_deviation)}
          </p>
        ) : null}

        <div className="card__actions">
          {listing.latitude !== null && listing.longitude !== null ? (
            <button
              type="button"
              className="button button--ghost"
              onClick={() => onSelect?.(listing)}
            >
              Ver en el mapa
            </button>
          ) : null}
          {listing.url ? (
            <a className="button button--ghost" href={listing.url} target="_blank" rel="noreferrer">
              Ver anuncio
            </a>
          ) : null}
        </div>
      </div>
    </article>
  );
}
