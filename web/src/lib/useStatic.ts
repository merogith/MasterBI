import { useEffect, useState } from 'preact/hooks';

/** True while the frozen demo is answering requests rather than a real server.
 *
 *  `tools/static_shim.js` owns the flag and announces when its probe settles,
 *  which is after the app has already mounted — so this listens rather than
 *  reading once. */
export function useIsStatic(): boolean {
  const [isStatic, setIsStatic] = useState(() => window.KPI_STATIC === true);
  useEffect(() => {
    const sync = () => setIsStatic(window.KPI_STATIC === true);
    window.addEventListener('kpi-static-change', sync);
    return () => window.removeEventListener('kpi-static-change', sync);
  }, []);
  return isStatic;
}
