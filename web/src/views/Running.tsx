import { cancelRun, type Progress } from '../lib/api';

/* The running screen. Every number here comes from the engine's own progress
   events — 0.6 replaced a hardcoded five-step list that ticked over after the
   run had already finished, so nothing on this screen is invented locally. */
export function Running({ company, progress, runId, cancelling, onCancel }: {
  company: string;
  progress: Progress | null | undefined;
  runId: string;
  cancelling: boolean;
  onCancel: () => void;
}) {
  const done = progress?.done ?? 0;
  const total = progress?.total ?? 0;
  const percent = total > 0 ? Math.round((done / total) * 100) : 0;
  const eta = progress?.eta_seconds;

  return (
    <section class="view view-center" id="view-running">
      <div class="run-card">
        <h2 id="run-company">{company || 'Generating…'}</h2>

        <div class="run-bar" role="progressbar" aria-valuemin={0} aria-valuemax={100}
             aria-valuenow={percent} id="run-bar">
          <div class="run-bar-fill" id="run-bar-fill" style={{ width: `${percent}%` }} />
        </div>

        <p class="run-stage" id="run-stage" aria-live="polite">
          {progress?.current?.label ?? 'Queued'}
        </p>
        <p class="run-count" id="run-count">
          {total > 0 && `${done} of ${total} stages`}
          {eta !== undefined && eta > 0 && ` · about ${Math.round(eta)}s left`}
        </p>

        <details class="run-detail">
          <summary id="run-detail-summary">All stages</summary>
          <ol class="run-steps" id="run-steps">
            {(progress?.stages ?? []).map((stage) => (
              <li key={stage.stage} class={`step ${stage.state}`}>
                {stage.label}
                {stage.state === 'reused' && <span class="step-note"> · reused</span>}
              </li>
            ))}
          </ol>
        </details>

        <button class="ghost" id="run-cancel" disabled={cancelling}
                onClick={() => { void cancelRun(runId); onCancel(); }}>
          Cancel
        </button>
        <p class="run-note" id="run-note" hidden={!cancelling}>
          Stopping after the current stage — finished stages are kept.
        </p>
      </div>
    </section>
  );
}
