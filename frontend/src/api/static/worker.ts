/**
 * El hilo donde viven los datos.
 *
 * Los 149.923 anuncios se descargan, se decodifican y se consultan **aquí**, en
 * un worker, y no en el hilo de la interfaz. La razón no es que las consultas
 * sean lentas —un filtro son unos milisegundos— sino que algunas no lo son:
 * ordenar 149.923 precios para sacar cinco percentiles, o agregar el conjunto
 * entero en celdas, es tiempo suficiente para que se note en una animación de
 * Leaflet. Fuera del hilo principal, ese coste no puede tocar el mapa.
 *
 * Y hay un segundo motivo, más sutil: los `TypedArray` del paquete son 8,7 MB
 * que se quedan vivos toda la sesión. Aquí no compiten con el DOM por el
 * recolector del hilo principal.
 */

import { Dataset } from "./dataset";
import { fetchPayload, PayloadError } from "./payload";
import type { Request, Response } from "./protocol";
import { QueryEngine } from "./query";

/** Un 404 con nombre, para que el cliente pueda darle el mismo estatus que la API. */
class NotFound extends Error {}

/**
 * La carga, como promesa y no como resultado.
 *
 * Toda operación la espera, así que una consulta que llegue antes de que el
 * paquete haya bajado no falla: se encola detrás de la descarga. Eso importa
 * porque la primera petición de anuncios sale en cuanto Leaflet mide su
 * contenedor, que es antes de que 4 MB hayan cruzado la red.
 */
let loading: Promise<QueryEngine> | null = null;

function load(url: string): Promise<QueryEngine> {
  if (!loading) {
    loading = fetchPayload(url).then(
      (payload) => new QueryEngine(new Dataset(payload)),
      (cause: unknown) => {
        // Se olvida el intento fallido para que un reintento vuelva a pedirlo,
        // en vez de quedarse con la promesa rechazada para siempre.
        loading = null;
        throw cause;
      },
    );
  }
  return loading;
}

async function handle(request: Request): Promise<unknown> {
  if (request.op === "load") {
    const engine = await load(request.payloadUrl);
    // Se devuelve lo que la pantalla de carga necesita, no el paquete: 8,7 MB
    // de vuelta por un mensaje serían una copia entera para nada.
    return { count: engine.total };
  }

  if (!loading) throw new Error("El worker de datos no ha recibido su paquete");
  const engine = await loading;

  switch (request.op) {
    case "listings":
      return engine.page(request.filters, request.limit, request.offset, request.order);
    case "stats":
      return engine.stats(request.filters, request.bins);
    case "map":
      return engine.map(request.filters, request.zoom, request.heat);
    case "facets":
      return engine.facets;
    case "listing": {
      const listing = engine.listing(request.globalId);
      if (!listing) throw new NotFound(`No existe el anuncio ${request.globalId}`);
      return listing;
    }
  }
}

self.onmessage = (event: MessageEvent<Request>) => {
  const request = event.data;
  handle(request).then(
    (value) => {
      const response: Response = { id: request.id, ok: true, value };
      self.postMessage(response);
    },
    (cause: unknown) => {
      const response: Response = {
        id: request.id,
        ok: false,
        // Un id que no existe es un 404 y un paquete que no baja es un 503, igual
        // que en la API: la interfaz ya sabe qué hacer con cada uno.
        status: cause instanceof NotFound ? 404 : cause instanceof PayloadError ? 503 : 500,
        message: cause instanceof Error ? cause.message : "Error desconocido",
      };
      self.postMessage(response);
    },
  );
};
