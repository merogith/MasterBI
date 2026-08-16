import type { ComponentChildren } from 'preact';

/* Loading, failed and empty are three different things.
 *
 * They were one: `<p class="empty">…</p>`, used in thirteen places across six
 * files for "still fetching", "the request failed" and "there is genuinely
 * nothing here". Identical grey paragraph in all three, so a user could not
 * tell a slow network from a broken server from an empty list — and a screen
 * reader was told nothing at all, because none of them carried a live region.
 *
 * Each state now looks different, says what to do next where there is
 * something to do, and announces itself: `role="status"` for progress,
 * `role="alert"` for failure. Empty stays quiet — nothing has changed for a
 * screen reader to be interrupted about.
 */

export function Loading({ label = 'Loading…' }: { label?: string }) {
  return (
    <p class="state state-loading" role="status">
      <span class="state-spinner" aria-hidden="true" />
      {label}
    </p>
  );
}

export function Failed({ message, onRetry }: {
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div class="state state-failed" role="alert">
      <span class="state-icon" aria-hidden="true">!</span>
      <span class="state-text">{message}</span>
      {onRetry && (
        <button class="ghost" type="button" onClick={onRetry}>Try again</button>
      )}
    </div>
  );
}

export function Empty({ title, children }: {
  title: string;
  children?: ComponentChildren;
}) {
  return (
    <div class="state state-empty">
      <span class="state-title">{title}</span>
      {children && <span class="state-text">{children}</span>}
    </div>
  );
}
