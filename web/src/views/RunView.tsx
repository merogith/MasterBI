import { useEffect, useRef, useState } from 'preact/hooks';
import { getRun, type Run } from '../lib/api';
import { navigate } from '../lib/router';
import { Results } from './Results';
import { Running } from './Running';

const POLL_FIRST_MS = 400;
const POLL_MAX_MS = 3000;

/* One URL for a run, whatever state it is in. The legacy front end had no URL
   for a run at all, so a reload during a render dropped the user on the home
   screen with the work still going on invisibly behind them. */
export function RunView({ runId }: { runId: string }) {
  const [run, setRun] = useState<Run | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => {
    let live = true;
    let wait = POLL_FIRST_MS;

    const tick = async () => {
      try {
        const next = await getRun(runId);
        if (!live) return;
        setRun(next);
        if (next.status === 'done' || next.status === 'cancelled'
            || next.status === 'error' || next.status === 'missing') {
          return;
        }
        wait = Math.min(Math.round(wait * 1.25), POLL_MAX_MS);
        timer.current = setTimeout(tick, wait) as unknown as number;
      } catch (err) {
        if (live) setError((err as Error).message);
      }
    };

    void tick();
    return () => {
      live = false;
      clearTimeout(timer.current);
    };
  }, [runId]);

  if (error) {
    return (
      <section class="view view-center" id="view-error">
        <div class="run-card">
          <h2>Lost contact with the server</h2>
          <p class="lede">{error}</p>
          <button class="primary" onClick={() => navigate('/')}>Back home</button>
        </div>
      </section>
    );
  }

  if (run === null) {
    return (
      <section class="view view-center" id="view-running">
        <div class="run-card"><p class="run-stage" id="run-stage">Loading…</p></div>
      </section>
    );
  }

  if (run.status === 'done' && run.summary) {
    return <Results summary={run.summary} />;
  }

  // A run that stopped is not a run that failed to exist. 0.7 made these
  // addressable after a restart; saying so here is the point of having done it.
  if (run.status === 'cancelled' || run.status === 'error' || run.status === 'missing') {
    return (
      <section class="view view-center" id="view-stopped">
        <div class="run-card">
          <h2>{run.status === 'missing' ? 'Artifacts deleted' : `Run ${run.status}`}</h2>
          <p class="lede">
            {run.status === 'cancelled' && run.cancelled_stage
              ? `Stopped before ${run.cancelled_stage}. Finished stages were kept,
                 so starting again is quicker.`
              : run.error ?? ''}
          </p>
          <button class="primary" onClick={() => navigate('/')}>Back home</button>
        </div>
      </section>
    );
  }

  return (
    <Running runId={runId} company={run.company ?? ''} progress={run.progress}
             cancelling={cancelling} onCancel={() => setCancelling(true)} />
  );
}
