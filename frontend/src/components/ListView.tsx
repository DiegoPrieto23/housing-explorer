import { count } from "../format";
import type { Listing, ListingPage } from "../types/listing";
import { globalId } from "../types/listing";
import ListingCard from "./ListingCard";

interface ListViewProps {
  page: ListingPage | null;
  loading: boolean;
  error: string | null;
  pageSize: number;
  pageIndex: number;
  onPageChange: (index: number) => void;
  selectedId: string | null;
  onSelect: (listing: Listing) => void;
  isFavourite: (globalId: string) => boolean;
  onToggleFavourite: (globalId: string) => void;
  /** Mensaje propio cuando no hay nada: "sin favoritos" no es "sin resultados". */
  emptyMessage?: string;
}

export default function ListView({
  page,
  loading,
  error,
  pageSize,
  pageIndex,
  onPageChange,
  selectedId,
  onSelect,
  isFavourite,
  onToggleFavourite,
  emptyMessage,
}: ListViewProps) {
  if (error) {
    return <p className="notice notice--error">{error}</p>;
  }

  // `page` is null only before the first response ever lands; afterwards the
  // previous page stays visible while the next one loads.
  if (!page) {
    return <p className="notice">Cargando anuncios…</p>;
  }

  if (page.total === 0) {
    return (
      <p className="notice">
        {emptyMessage ??
          "Ningún anuncio cumple estos filtros. Prueba a ampliar el rango de precio o a quitar alguna condición."}
      </p>
    );
  }

  const pages = Math.ceil(page.total / pageSize);
  const first = page.offset + 1;
  const last = page.offset + page.items.length;

  return (
    <div className={`list${loading ? " is-loading" : ""}`}>
      <div className="cards">
        {page.items.map((listing) => (
          <ListingCard
            key={globalId(listing)}
            listing={listing}
            active={globalId(listing) === selectedId}
            onSelect={onSelect}
            favourite={isFavourite(globalId(listing))}
            onToggleFavourite={onToggleFavourite}
          />
        ))}
      </div>

      <nav className="pager" aria-label="Paginación">
        <button
          type="button"
          className="button"
          disabled={pageIndex === 0}
          onClick={() => onPageChange(pageIndex - 1)}
        >
          ← Anterior
        </button>
        <span className="muted">
          {count(first)}–{count(last)} de {count(page.total)}
          <span className="pager__pages">
            {" "}
            · página {count(pageIndex + 1)} de {count(pages)}
          </span>
        </span>
        <button
          type="button"
          className="button"
          disabled={pageIndex + 1 >= pages}
          onClick={() => onPageChange(pageIndex + 1)}
        >
          Siguiente →
        </button>
      </nav>
    </div>
  );
}
