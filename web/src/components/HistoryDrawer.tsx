import { useEffect, useState } from 'preact/hooks';
import { listRuns, rerunRun, type RunRow } from '../lib/api';
import { navigate } from '../lib/router';
import { Empty, Failed, Loading } from './State';

/* Every run this installation knows about, whatever became of it.
 *
 * 0.7 made cancelled and failed runs visible here again — before the run store
 * they were rebuilt by globbing `summary.json`, which a cancelled run
 * deliberately never writes, so they vanished along with the stages 0.6 kept on
 * disk. Each row offers the action that fits its state: Open for a finished
 * run, Resume for one that stopped, nothing for a run whose artifacts are gone.
 */
function meta(run: RunRow): string {
  const bits = [run.status, run.mode].filter(Boolean) as string[];
  if (run.status === 'cancelled' && run.cancelled_stage) {
    bits.push(`stopped before ${run.cancelled_stage}`);
  }
  if (run.status === 'missing') bits.push('artifacts deleted');
  return bits.join(' · ');
}

export function HistoryDrawer({ open, onClose }: {
  open: boolean;
  onClose: () => void;
}) {
  const [runs, setRuns] = useState<RunRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setRuns(null);
    setError(null);
    listRuns().then(setRuns, (err: Error) => setError(err.message));
  }, [open]);

  async function resume(runId: string) {
    try {
      await rerunRun(runId);
      onClose();
      navigate(`/runs/${runId}`);
    } catch (err) {
      setError((err as Error).message);
    }
  }

  function open_(runId: string) {
    onClose();
    navigate(`/runs/${runId}`);
  }

  return (
    <>
      <div class="drawer-scrim" id="drawer-scrim" hidden={!open} onClick={onClose} />
      {/* Escape on the panel, with focus taken as it opens, rather than a
          listener registered in an effect — the drawer is dismissable from the
          moment it is visible. Same defect the record sheet had, found on CI
          rather than locally, because Preact flushes effects after paint. */}
      <aside class="drawer" id="drawer" hidden={!open} aria-label="Recent runs"
             tabIndex={-1} ref={(node) => { if (open) node?.focus(); }}
             onKeyDown={(e) => { if (e.key === 'Escape') onClose(); }}>
        <div class="drawer-head">
          <h2>Recent runs</h2>
          <button class="ghost" id="drawer-close" aria-label="Close" onClick={onClose}>
            ✕
          </button>
        </div>
        <div id="drawer-list">
          {error && <Failed message={error} />}
          {!error && runs === null && <Loading label="Loading your runs…" />}
          {runs?.length === 0 && (
            <Empty title="No runs yet">
              Anything you generate appears here, and stays addressable by URL.
            </Empty>
          )}
          {runs?.map((run) => (
            <div class="run-row" key={run.run_id}>
              <div>
                <div class="r-name">{run.company || 'Untitled'}</div>
                <div class="r-meta">{meta(run)}</div>
              </div>
              {run.status === 'done' && (
                <button class="ghost" data-open-run={run.run_id}
                        onClick={() => open_(run.run_id)}>
                  Open
                </button>
              )}
              {(run.status === 'cancelled' || run.status === 'error') && run.resumable && (
                <button class="ghost" data-resume-run={run.run_id}
                        onClick={() => void resume(run.run_id)}>
                  Resume
                </button>
              )}
            </div>
          ))}
        </div>
      </aside>
    </>
  );
}
