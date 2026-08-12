/* Reading and writing the run spec by dotted path.
 *
 * The Studio's controls each own one field of a large nested model. Binding
 * them by path keeps the panels declarative and means adding a field is one
 * line rather than a getter, a setter and a re-render.
 */
import type { Spec } from './api';

export function getPath(spec: Spec, path: string): unknown {
  return path.split('.').reduce<unknown>(
    (node, key) => (node == null ? undefined : (node as Spec)[key]), spec);
}

/** Returns a new spec — the Studio holds it in state, so mutating in place
 *  would leave Preact unable to see that anything changed. */
export function setPath(spec: Spec, path: string, value: unknown): Spec {
  const keys = path.split('.');
  const copy: Spec = { ...spec };
  let node = copy;
  for (const key of keys.slice(0, -1)) {
    node[key] = { ...(node[key] ?? {}) };
    node = node[key];
  }
  node[keys[keys.length - 1] as string] = value;
  return copy;
}

/** Toggle membership of a list held at `path`, preserving order. */
export function toggleIn(spec: Spec, path: string, value: string,
                         present: string[]): Spec {
  const next = present.includes(value)
    ? present.filter((item) => item !== value)
    : [...present, value];
  return setPath(spec, path, next);
}

export const titleCase = (text: string): string =>
  text.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());
