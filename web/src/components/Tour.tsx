import { useEffect, useState } from 'preact/hooks';

/* Four steps, once, on the screen where there is something to point at.
 *
 * The research is unambiguous and it shaped every constraint here: tours over
 * five steps drop off sharply and over eight are abandoned by 84%, and a tour
 * fired on arrival — before the user has anything of their own — is dismissed
 * without being read. So this is **four steps, triggered by reaching a finished
 * board pack**, not by landing on the home page.
 *
 * Dismissible at any point, and never shown again: the flag is written on the
 * first render, not on completion, because someone who closes it at step one
 * has told us more clearly than someone who finishes it.
 */
const SEEN_KEY = 'masterbi.tour.results';

export interface TourStep {
  title: string;
  body: string;
  /** Element to point at. A step whose target is absent is skipped rather than
   *  pointing at nothing — the download grid is there on every run, the
   *  findings list is not. */
  target?: string;
}

function seen(key: string): boolean {
  try {
    return window.localStorage.getItem(key) === '1';
  } catch {
    // No storage means we cannot remember a dismissal, and a tour that returns
    // every visit is worse than one that never appears.
    return true;
  }
}

function remember(key: string): void {
  try {
    window.localStorage.setItem(key, '1');
  } catch { /* see above */ }
}

export function Tour({ steps, storageKey = SEEN_KEY }: {
  steps: TourStep[];
  storageKey?: string;
}) {
  const [open, setOpen] = useState(() => !seen(storageKey));
  const [index, setIndex] = useState(0);
  const [live, setLive] = useState<TourStep[]>([]);

  // Which steps have something to point at, resolved **after mount**.
  //
  // This was computed during render, and the browser test caught what that
  // means: the targets are rendered by the same pass, so `querySelector` found
  // none of them, every step filtered out, and the tour silently never
  // appeared. A component that decides its own existence from the DOM has to
  // wait for the DOM.
  useEffect(() => {
    setLive(steps.filter(
      (step) => !step.target || document.querySelector(step.target) !== null));
  }, [steps]);

  // Written on first render rather than on completion: closing at step one is
  // a clearer signal than finishing, and both must mean "do not show again".
  useEffect(() => {
    if (open) remember(storageKey);
  }, [open, storageKey]);

  if (!open || live.length === 0) return null;

  const position = Math.min(index, live.length - 1);
  const step = live[position] as TourStep;
  const last = position === live.length - 1;

  return (
    // Escape closes it. A modal-ish overlay with no keyboard exit is a trap,
    // and this one appears unbidden.
    //
    // Handled on the element, with focus taken in a ref callback, rather than
    // by a `keydown` listener registered in an effect. CI caught the effect
    // version of exactly this on the record sheet: the panel is painted and
    // clickable while the listener that dismisses it does not exist yet,
    // because Preact defers effects. Something a user can see must already
    // work.
    <aside class="tour" id="tour" role="dialog" aria-label="Quick tour"
           aria-live="polite" tabIndex={-1} ref={(node) => node?.focus()}
           onKeyDown={(e) => { if (e.key === 'Escape') setOpen(false); }}>
      <div class="tour-head">
        <strong>{step.title}</strong>
        <span class="tour-count">{position + 1} of {live.length}</span>
      </div>
      <p class="tour-body">{step.body}</p>
      <div class="tour-nav">
        <button class="linkish" id="tour-dismiss" type="button"
                onClick={() => setOpen(false)}>
          Dismiss
        </button>
        <div>
          {position > 0 && (
            <button class="ghost" type="button"
                    onClick={() => setIndex(position - 1)}>Back</button>
          )}
          <button class="primary" id="tour-next" type="button"
                  onClick={() => (last ? setOpen(false) : setIndex(position + 1))}>
            {last ? 'Got it' : 'Next'}
          </button>
        </div>
      </div>
    </aside>
  );
}

/** What a first-time reader of a finished pack needs, and nothing else.
 *
 *  Four, deliberately: the number of things worth saying about this screen,
 *  and also the ceiling the research puts on a tour anyone finishes. */
export const RESULTS_TOUR: TourStep[] = [
  {
    title: 'This is the whole pack',
    body: 'Nine artifacts — an interactive dashboard, a board report, a deck, '
      + 'a workbook and the data behind them. Every number was computed from '
      + 'the profile, not written by a model.',
    target: '#res-downloads',
  },
  {
    title: 'The headline KPIs',
    body: 'Chosen for this sector, size and objective. The colour is a status '
      + 'against a target or a benchmark, never decoration — and it is always '
      + 'paired with a glyph so it survives being printed or colour-blind.',
    target: '#res-tiles',
  },
  {
    title: 'What the detectors found',
    body: 'Each line is a pattern found in the data, ranked by severity. These '
      + 'are computed, so they are the same in the dashboard, the report and '
      + 'the deck.',
    target: '#res-findings',
  },
  {
    title: 'Change anything and re-run',
    body: 'The Studio edits the spec behind this run — KPIs, cleaning, '
      + 'calculated columns, branding, sections. A re-run rebuilds only what '
      + 'your change affected.',
    target: '#res-adjust',
  },
];
