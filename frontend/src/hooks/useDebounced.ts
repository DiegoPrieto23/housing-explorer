import { useEffect, useState } from "react";

/**
 * The value as it was `delay` ms ago, once it stops changing.
 *
 * Typing "250000" into a price box is five state changes; without this the
 * browser fires five requests and the backend runs five percentile scans over
 * 150k rows to answer four questions nobody asked.
 */
/** Teclear. Corto, porque el usuario espera ver el efecto de lo que escribe. */
export const TYPING_DELAY = 300;

/**
 * Mover o hacer zoom en el mapa. Más largo a propósito.
 *
 * Un arrastre de 30 pasos ya se colapsa en una sola petición con 300 ms, pero
 * explorar el mapa son varios arrastres cortos seguidos, y con 300 ms cada
 * pausa entre ellos dispara una tanda: medidos 9 peticiones para tres
 * arrastres encadenados. A 600 ms esos tres se juntan en una.
 *
 * El coste es que el mapa tarda 300 ms más en refrescarse tras el último
 * movimiento; a cambio, el resultado anterior sigue en pantalla mientras tanto,
 * así que no se ve un hueco, solo una actualización algo más tardía.
 */
export const VIEWPORT_DELAY = 600;

export function useDebounced<T>(value: T, delay = TYPING_DELAY): T {
  const [settled, setSettled] = useState(value);

  useEffect(() => {
    const timer = window.setTimeout(() => setSettled(value), delay);
    return () => window.clearTimeout(timer);
  }, [value, delay]);

  return settled;
}
