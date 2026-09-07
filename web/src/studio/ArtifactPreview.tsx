import { useEffect, useRef, useState } from 'preact/hooks';
import { previewPages } from '../lib/api';

/* The Design panel previewed the palette, and the palette is not the
   artifact. Swatches and contrast ratios answer "is this colour legible",
   which is worth answering and is not what somebody opening this panel wants
   to know: they want to see the thing they are about to send to a board.

   So this shows the real first two pages, rendered by the same
   `render_report` the run calls. A preview drawn by a second implementation
   is a mock, and a mock drifts from the document it claims to show — which
   is the whole failure mode this panel already had, one level up. */

/** Long enough that dragging a colour picker does not queue a render per
 *  frame, short enough to feel like it is following you. The render itself
 *  measured ~0.1s against a warm run. */
const DEBOUNCE_MS = 400;

export function ArtifactPreview({ runId, design, readOnly }: {
  runId: string | null;
  design: unknown;
  readOnly?: boolean;
}) {
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // The URL currently shown, so the effect can revoke the *previous* blob
  // rather than the one it just made. Without this every colour change leaks
  // a PDF for the lifetime of the tab.
  const shown = useRef<string | null>(null);

  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    setBusy(true);
    const timer = setTimeout(() => {
      previewPages(runId, design)
        .then((next) => {
          if (cancelled) { URL.revokeObjectURL(next); return; }
          if (shown.current) URL.revokeObjectURL(shown.current);
          shown.current = next;
          setUrl(next);
          setError(null);
        })
        .catch((err: Error) => { if (!cancelled) setError(err.message); })
        .finally(() => { if (!cancelled) setBusy(false); });
    }, DEBOUNCE_MS);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [runId, JSON.stringify(design)]);

  // Revoke on unmount too: closing the Studio should not leave the last PDF
  // pinned in memory.
  useEffect(() => () => {
    if (shown.current) URL.revokeObjectURL(shown.current);
  }, []);

  /* No run, no preview, and say so rather than drawing a plausible cover.
     The cover's figure is this company's north star and the summary's
     bullets are this run's findings; inventing either would make the panel
     show a different document from the one it is editing. 5.1's "no plan
     means no variance", applied to a page. */
  if (!runId) {
    return (
      <div id="artifact-preview" class="artifact-preview">
        <p class="empty" role="status">
          The cover and summary page appear here once there is a run to
          render. The colours above apply either way.
        </p>
      </div>
    );
  }

  return (
    <div id="artifact-preview" class="artifact-preview">
      <div class="preview-head">
        <h3 class="studio-sub">Your report, first two pages</h3>
        {busy && <span class="hint" role="status">rendering…</span>}
      </div>
      {error
        ? <p class="warn" role="alert">{error}</p>
        : url
          /* **The viewer's furniture is not the document.** Left to its
             defaults the embedded viewer drew a toolbar captioned with the
             blob's UUID, page and zoom controls, download and print buttons,
             and a thumbnail rail — and squeezed the page the user came to
             look at into about 60% of the frame. Found by screenshotting the
             panel after every assertion on it passed.

             These are the PDF open parameters, so a viewer that does not
             support them ignores the fragment and shows the document, which
             is the right way to fail. `view=Fit` is chosen so the
             whole page is visible: `FitH` fits the width, which at this
             frame height put the north-star figure and the footer below the
             fold — and the footer is one of the three fields this panel
             exists to make visible. A design preview you have to scroll to
             see the thing you just edited is not previewing it. */
          ? <object class="preview-frame"
                    data={`${url}#toolbar=0&navpanes=0&statusbar=0&view=Fit`}
                    type="application/pdf"
                    aria-label="Cover and executive summary as they will print">
              <p class="hint">
                <a href={url} target="_blank" rel="noreferrer">Open the preview</a>
                {' '}— this browser will not display a PDF inline.
              </p>
            </object>
          : <p class="empty" role="status">Rendering the first two pages…</p>}
      <p class="hint">
        Rendered by the same code that writes <code>report.pdf</code>, so what
        is here is what prints.{readOnly ? ' This demo run is read-only.' : ''}
      </p>
    </div>
  );
}
