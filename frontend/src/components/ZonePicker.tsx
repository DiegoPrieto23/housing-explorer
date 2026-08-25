import { useEffect, useMemo, useRef, useState } from "react";

import { count } from "../format";
import type { NeighbourhoodFacet, ZoneFacet } from "../types/listing";

interface ZonePickerProps {
  zones: ZoneFacet[];
  /** Ciudad entera seleccionada, o null. */
  city: string | null;
  /** Barrios seleccionados, por `LOCATIONID`. */
  selected: string[];
  onCityChange: (city: string | null) => void;
  onToggleNeighbourhood: (id: string) => void;
  onClearNeighbourhoods: () => void;
}

/**
 * Texto comparable: sin acentos y en minúsculas.
 *
 * Buscar «malasana» tiene que encontrar «Malasaña», y buscar «jeronimos» tiene
 * que encontrar «Jerónimos». Quien teclea deprisa no pone los acentos, y un
 * buscador que exige la ñ para encontrar un barrio con ñ no es un buscador.
 */
function normalise(text: string): string {
  return text
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase();
}

/** Cuántos barrios de esta ciudad están marcados. */
function selectedIn(zone: ZoneFacet, selected: Set<string>): number {
  return zone.neighbourhoods.reduce(
    (total, entry) => total + (selected.has(entry.id) ? 1 : 0),
    0,
  );
}

/**
 * «Dónde buscar»: la ciudad entera, o barrios sueltos dentro de ella.
 *
 * Sustituye a un `<select>` de tres opciones. Con 277 barrios un desplegable
 * nativo deja de servir por dos motivos: no anida, así que «Sol» y «Sants» se
 * mezclarían en una lista plana sin decir de qué ciudad son; y no permite
 * elegir varios sin `multiple`, que en la práctica obliga a mantener pulsado
 * Ctrl y no enseña qué llevas elegido.
 *
 * Las dos elecciones son **excluyentes**. Una ciudad entera y unos barrios de
 * esa ciudad son preguntas distintas, y tener las dos puestas obligaría a
 * adivinar cuál manda. Elegir barrios suelta la ciudad y elegir la ciudad
 * suelta los barrios, que además es lo que el usuario espera al ver que la
 * cuenta de resultados cambia.
 *
 * Ojo con una diferencia que sí se nota: una ciudad **no** es la suma de sus
 * barrios. 230 anuncios caen fuera de todos los polígonos —71 en Madrid, 119 en
 * Barcelona, 40 en Valencia—, porque el dataset cubre el área metropolitana y
 * los polígonos paran en el término municipal. Marcar los 135 barrios de Madrid
 * devuelve 75.733 anuncios y marcar «Madrid» devuelve 75.804. Por eso la ciudad
 * sigue siendo una opción propia y no un «marcar todos».
 */
export default function ZonePicker({
  zones,
  city,
  selected,
  onCityChange,
  onToggleNeighbourhood,
  onClearNeighbourhoods,
}: ZonePickerProps) {
  const [query, setQuery] = useState("");
  const [expanded, setExpanded] = useState<string[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);

  const selectedSet = useMemo(() => new Set(selected), [selected]);

  /** id -> barrio, para pintar las fichas de lo elegido sin volver a buscar. */
  const byId = useMemo(() => {
    const map = new Map<string, NeighbourhoodFacet>();
    for (const zone of zones) {
      for (const entry of zone.neighbourhoods) map.set(entry.id, entry);
    }
    return map;
  }, [zones]);

  const needle = normalise(query.trim());

  /**
   * Lo que se pinta, ya filtrado por el texto.
   *
   * Si lo que se ha escrito coincide con el nombre de la ciudad, la ciudad sale
   * con todos sus barrios: buscar «valencia» tiene que enseñar Valencia entera,
   * no cero resultados porque ningún barrio se llame así.
   */
  const visible = useMemo(() => {
    if (!needle) {
      return zones.map((zone) => ({ zone, matches: zone.neighbourhoods }));
    }

    return zones
      .map((zone) => {
        if (normalise(zone.value).includes(needle)) {
          return { zone, matches: zone.neighbourhoods };
        }
        return {
          zone,
          matches: zone.neighbourhoods.filter((entry) =>
            normalise(entry.name).includes(needle),
          ),
        };
      })
      .filter((entry) => entry.matches.length > 0);
  }, [zones, needle]);

  const totalMatches = visible.reduce((total, entry) => total + entry.matches.length, 0);

  // Al buscar, todo abierto: esconder una coincidencia detrás de un triángulo
  // sería esconder justo lo que se ha pedido ver. Al vaciar la caja, se vuelve
  // a lo que hubiera abierto, que es donde el usuario estaba mirando.
  const openCities = needle ? visible.map((entry) => entry.zone.value) : expanded;

  // La ciudad elegida se abre sola, para que sus barrios estén a un clic.
  useEffect(() => {
    if (city !== null) setExpanded((previous) => (previous.includes(city) ? previous : [city]));
  }, [city]);

  const toggleCity = (name: string) =>
    setExpanded((previous) =>
      previous.includes(name) ? previous.filter((item) => item !== name) : [...previous, name],
    );

  const everything = city === null && selected.length === 0;

  return (
    <div className="picker">
      <div className="picker__search">
        <input
          ref={inputRef}
          type="search"
          value={query}
          placeholder="Escribe una ciudad o un barrio…"
          aria-label="Buscar barrio"
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key !== "Enter") return;
            // Enter elige la primera coincidencia. Con 277 barrios, escribir
            // "sol" y pulsar Enter debe bastar; obligar a bajar la mano al
            // ratón para dar en una fila de 24 píxeles no.
            event.preventDefault();
            const first = visible[0]?.matches[0];
            if (first) {
              onToggleNeighbourhood(first.id);
              setQuery("");
            }
          }}
        />
        {query ? (
          <button
            type="button"
            className="picker__clear"
            aria-label="Borrar la búsqueda"
            onClick={() => {
              setQuery("");
              inputRef.current?.focus();
            }}
          >
            ×
          </button>
        ) : null}
      </div>

      {query && totalMatches === 0 ? (
        <p className="muted picker__empty">Ningún barrio se llama así.</p>
      ) : null}

      {selected.length > 0 ? (
        <div className="picker__chips">
          {selected.map((id) => {
            const entry = byId.get(id);
            return (
              <button
                key={id}
                type="button"
                className="chip"
                onClick={() => onToggleNeighbourhood(id)}
                title="Quitar este barrio"
              >
                {entry ? entry.name : id}
                <span className="chip__x" aria-hidden="true">
                  ×
                </span>
              </button>
            );
          })}
          {selected.length > 1 ? (
            <button
              type="button"
              className="chip chip--clear"
              onClick={onClearNeighbourhoods}
            >
              Quitar los {selected.length}
            </button>
          ) : null}
        </div>
      ) : null}

      <ul className="picker__list">
        <li>
          <button
            type="button"
            className={`picker__row picker__row--all${everything ? " is-active" : ""}`}
            aria-pressed={everything}
            onClick={() => {
              onCityChange(null);
              onClearNeighbourhoods();
            }}
          >
            Toda España
          </button>
        </li>

        {visible.map(({ zone, matches }) => {
          const open = openCities.includes(zone.value);
          const chosen = selectedIn(zone, selectedSet);
          const wholeCity = city === zone.value;

          return (
            <li key={zone.value}>
              <div className={`picker__city${wholeCity ? " is-active" : ""}`}>
                <button
                  type="button"
                  className="picker__caret"
                  aria-expanded={open}
                  aria-label={`${open ? "Plegar" : "Desplegar"} los barrios de ${zone.value}`}
                  onClick={() => toggleCity(zone.value)}
                >
                  {open ? "▾" : "▸"}
                </button>
                <button
                  type="button"
                  className="picker__row"
                  aria-pressed={wholeCity}
                  onClick={() => onCityChange(wholeCity ? null : zone.value)}
                  title={`Buscar en todo ${zone.value}`}
                >
                  <span className="picker__name">{zone.value}</span>
                  <span className="picker__count">{count(zone.count)}</span>
                </button>
                {chosen > 0 ? <span className="picker__badge">{chosen}</span> : null}
              </div>

              {open ? (
                <ul className="picker__children">
                  {matches.map((entry) => {
                    const on = selectedSet.has(entry.id);
                    return (
                      <li key={entry.id}>
                        <label
                          className={`picker__leaf${on ? " is-active" : ""}${
                            entry.count === 0 ? " is-empty" : ""
                          }`}
                        >
                          <input
                            type="checkbox"
                            checked={on}
                            onChange={() => onToggleNeighbourhood(entry.id)}
                          />
                          <span className="picker__name">{entry.name}</span>
                          <span className="picker__count">{count(entry.count)}</span>
                        </label>
                      </li>
                    );
                  })}
                </ul>
              ) : null}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
