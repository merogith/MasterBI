/* Keyboard behaviour every overlay in this app owes, in one place.
 *
 * Three things, and they are separable rather than one feature:
 *
 *   * **Escape closes it.** 3.5b established where that has to live — on the
 *     element, in the same commit that puts the element on screen, never in an
 *     effect. Preact flushes effects after paint, so an effect-registered
 *     listener leaves a window in which the panel is visible, focused and
 *     clickable while the thing that dismisses it does not exist. That window
 *     closes too fast to see locally and did not close on a CI runner.
 *   * **Focus lands on it.** Also a ref callback rather than an effect, for the
 *     same reason and one more: Escape is handled *on the element*, so a panel
 *     that never receives focus never receives the key either. The two halves
 *     are one mechanism.
 *   * **Focus does not leave it, and comes back afterwards.** The half 3.5b
 *     deferred, and it is what this module adds.
 *
 * The restore target is captured inside the ref callback, which is the only
 * moment it is knowable: a ref runs during commit, *before* `node.focus()`
 * moves anything, so `document.activeElement` is still whatever the user was
 * on when the overlay appeared. Read it one tick later and it is the overlay
 * itself.
 *
 * **The trap is not for every overlay, and applying it everywhere would be a
 * defect rather than thoroughness.** A trap is correct for a modal — something
 * with a scrim, where the page behind it is not meant to be reachable — and
 * wrong for a coach mark. The tour is a 340px panel in the bottom-right corner
 * with no scrim, pointing at live page elements; trapping focus inside it would
 * lock a keyboard user in the corner while the thing it is describing sits
 * behind, usable by everyone else. So `trap` is a parameter and the tour passes
 * false. This repo's recurring trap is a rule borrowed from one population and
 * applied to another; a focus trap on a non-modal is exactly that.
 */
import { useRef } from 'preact/hooks';

/* Tabbable descendants, in DOM order.
 *
 * `[tabindex="-1"]` is excluded deliberately: it is how an element is made
 * *programmatically* focusable without joining the tab ring, which is precisely
 * what the overlay root itself is. Including it would make the root both the
 * first and last stop and the cycle would never reach a button. */
const FOCUSABLE = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(', ');

function tabbable(root: HTMLElement): HTMLElement[] {
  // `getClientRects()` rather than the usual `offsetParent !== null`: it asks
  // whether the element occupies space, which is the actual question, instead
  // of inferring it from a positioning side-effect. Measured in Chromium
  // before choosing: `offsetParent` is null for a `position: fixed` element
  // *itself* (`.modal` and `.tour` both are) and not for its descendants, so
  // the two agree on everything queried here today — and they stop agreeing
  // the moment a nested element is made fixed, which is a change nobody would
  // connect to the focus trap.
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE))
    .filter((el) => el.getClientRects().length > 0);
}

function cycle(event: KeyboardEvent, root: HTMLElement): void {
  const items = tabbable(root);
  if (items.length === 0) {
    // Nothing to move to. Keeping focus on the root is better than letting Tab
    // walk out of a modal into a page the scrim says is not there.
    event.preventDefault();
    root.focus();
    return;
  }
  const first = items[0] as HTMLElement;
  const last = items[items.length - 1] as HTMLElement;
  const active = document.activeElement;

  // Backwards off the top wraps to the bottom. The root counts as "the top"
  // because focus starts there: it precedes its own children in tab order, so
  // a Shift+Tab from the freshly-opened panel would otherwise land behind it.
  if (event.shiftKey && (active === first || active === root)) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && active === last) {
    event.preventDefault();
    first.focus();
  }
}

export interface OverlayHandles {
  /** Put on the overlay root, which needs `tabIndex={-1}` to accept it. */
  ref: (node: HTMLElement | null) => void;
  /** Put on the same element. Handles Escape and, when trapping, Tab. */
  onKeyDown: (event: KeyboardEvent) => void;
}

/**
 * @param onClose  what Escape does. The overlay owns its own dismissal.
 * @param trap     true for a modal (scrim, page behind it unreachable), false
 *                 for a non-modal like the tour. See the module docstring.
 *
 * The returned `ref` is **stable across renders**, and that is load-bearing
 * rather than tidiness. Preact re-invokes an inline ref on every render —
 * `old(null)` then `new(node)` — so a closure written at the call site would
 * run the "overlay went away, give focus back" branch on every re-render,
 * flicking focus out of the panel and back for anyone watching. A stable
 * callback is invoked only when the node genuinely arrives or leaves.
 *
 * Which is also why every overlay using this must **mount and unmount** rather
 * than toggle `hidden`: unmount is the signal that focus should go home.
 */
export function useOverlay(onClose: () => void, trap = true): OverlayHandles {
  const restore = useRef<Element | null>(null);
  const node = useRef<HTMLElement | null>(null);
  const ref = useRef<((el: HTMLElement | null) => void) | null>(null);

  if (ref.current === null) {
    ref.current = (el: HTMLElement | null) => {
      if (el) {
        restore.current = document.activeElement;
        node.current = el;
        el.focus();
        return;
      }
      node.current = null;
      const back = restore.current as HTMLElement | null;
      restore.current = null;
      // `isConnected` because the overlay is frequently the thing that removes
      // its own opener — the drawer's Open button navigates the page away, and
      // calling `focus()` on a detached node silently sends focus to `<body>`,
      // which is worse than leaving it where the new screen put it.
      if (back && back.isConnected && typeof back.focus === 'function') back.focus();
    };
  }

  return {
    ref: ref.current,
    onKeyDown: (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onClose();
        return;
      }
      if (trap && event.key === 'Tab' && node.current) cycle(event, node.current);
    },
  };
}

/* ------------------------------------------------------------------ */

/** Arrow-key movement for a tab strip, per the ARIA authoring practices.
 *
 * Returns the id to select, or null if the key was not one of ours. Home and
 * End are included because a rail of eight is long enough that walking it one
 * step at a time is the reason people stop using the keyboard.
 *
 * Wraps at both ends: a tab strip is a ring, and stopping dead at the last
 * item reads as a broken key rather than as a boundary.
 */
export function tabStripKey<T extends string>(
  event: KeyboardEvent, ids: readonly T[], current: T,
  orientation: 'vertical' | 'horizontal' = 'vertical',
): T | null {
  const back = orientation === 'vertical' ? 'ArrowUp' : 'ArrowLeft';
  const forward = orientation === 'vertical' ? 'ArrowDown' : 'ArrowRight';
  const at = ids.indexOf(current);
  if (at < 0) return null;

  if (event.key === back) return ids[(at - 1 + ids.length) % ids.length] as T;
  if (event.key === forward) return ids[(at + 1) % ids.length] as T;
  if (event.key === 'Home') return ids[0] as T;
  if (event.key === 'End') return ids[ids.length - 1] as T;
  return null;
}
