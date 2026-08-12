/* Globals the hosted Pages build injects before this app loads.
 * Set by `tools/static_shim.js`; see the note in `lib/api.ts`. */
declare global {
  interface Window {
    /** True while the frozen static demo is answering requests. */
    KPI_STATIC?: boolean;
    /** Absolute base for artifact URLs — the site sub-path, or a loopback
     *  origin once the shim finds a local server. */
    KPI_FILES_BASE?: string;
  }
}

export {};
