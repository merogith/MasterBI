/* URL routing.
 *
 * The legacy front end has zero `pushState` calls: every screen change is a
 * `hidden` attribute flip, so Back leaves the app entirely and no run has a
 * link you can send anyone. That is the gap this closes.
 *
 * Hand-rolled rather than a router dependency. The whole requirement is
 * "match a path against a short table and re-render", which is the sixty lines
 * below; a router library would add a dependency and an API to learn in
 * exchange for features this app does not have — nested outlets, loaders,
 * transitions.
 *
 * Only routes that exist are listed. A table entry pointing at an unported
 * screen would be a link that 404s inside its own app.
 */
import { useEffect, useState } from 'preact/hooks';

/* Where this build is mounted. '' when FastAPI serves it from the domain root;
   '/MasterBI' for the GitHub Pages build, which lives under a repository
   sub-path. Every path the app deals with is written without it, and it is
   added back only at the two edges — the URL bar, and an `href`. */
const BASE = import.meta.env.BASE_URL.replace(/\/$/, '');

export interface Match {
  readonly name: string;
  readonly params: Readonly<Record<string, string>>;
  readonly path: string;
}

const ROUTES: ReadonlyArray<readonly [pattern: string, name: string]> = [
  ['/', 'home'],
  ['/samples', 'samples'],
  ['/survey', 'survey'],
  ['/data', 'builder'],
  ['/runs/:runId', 'run'],
  ['/runs/:runId/studio', 'studio'],
];

/** Trailing slashes are the same page; the root is the one exception. The
 *  mount point is stripped, so routes match the same in both builds. */
function normalise(pathname: string): string {
  let path = pathname;
  if (BASE && (path === BASE || path.startsWith(BASE + '/'))) {
    path = path.slice(BASE.length);
  }
  const trimmed = path.replace(/\/+$/, '');
  return trimmed === '' ? '/' : trimmed;
}

/** An app path as the browser should see it. Use for every `href`, so
 *  middle-click and copy-link produce a URL that actually resolves. */
export function href(to: string): string {
  return BASE + to;
}

function capture(pattern: string, path: string): Record<string, string> | null {
  const wanted = pattern.split('/');
  const given = path.split('/');
  if (wanted.length !== given.length) return null;

  const params: Record<string, string> = {};
  for (let i = 0; i < wanted.length; i++) {
    const segment = wanted[i] as string;
    const value = given[i] as string;
    if (segment.startsWith(':')) {
      if (value === '') return null;
      params[segment.slice(1)] = decodeURIComponent(value);
    } else if (segment !== value) {
      return null;
    }
  }
  return params;
}

export function match(pathname: string): Match {
  const path = normalise(pathname);
  for (const [pattern, name] of ROUTES) {
    const params = capture(pattern, path);
    if (params) return { name, params, path };
  }
  return { name: 'not-found', params: {}, path };
}

const listeners = new Set<() => void>();

export function navigate(to: string, { replace = false } = {}): void {
  if (normalise(to) === normalise(location.pathname)) return;
  history[replace ? 'replaceState' : 'pushState'](null, '', href(to));
  listeners.forEach((notify) => notify());
}

export function useRoute(): Match {
  const [route, setRoute] = useState(() => match(location.pathname));

  useEffect(() => {
    const sync = () => setRoute(match(location.pathname));
    listeners.add(sync);
    addEventListener('popstate', sync);
    return () => {
      listeners.delete(sync);
      removeEventListener('popstate', sync);
    };
  }, []);

  return route;
}

/** An anchor that routes instead of reloading — but still a real `href`, so
 *  middle-click, copy-link and open-in-new-tab all behave. */
export function onLinkClick(event: MouseEvent, to: string): void {
  if (event.defaultPrevented) return;
  if (event.button !== 0) return;
  if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  event.preventDefault();
  navigate(to);
}
