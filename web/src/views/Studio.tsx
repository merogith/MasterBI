import { useEffect, useRef, useState } from 'preact/hooks';
import {
  getAiStatus, getSpec, listCatalogKpis, listTables, getOptions, putSpec, rerunRun,
  type AiStatus, type CatalogKpi, type CatalogOptions, type PlanReport, type Spec,
} from '../lib/api';
import { navigate } from '../lib/router';
import {
  AiPanel, AnalysisPanel, CleanPanel, DesignPanel, KpiPanel, ModelPanel,
  OutputsPanel, SourcePanel,
} from '../studio/panels';

const STAGES = [
  ['source', 'Source'], ['clean', 'Clean'], ['model', 'Model'], ['kpis', 'KPIs'],
  ['analysis', 'Analysis'], ['design', 'Design'], ['outputs', 'Outputs'], ['ai', 'AI'],
] as const;

/** How long to sit on a keystroke before asking the server what it invalidated.
 *  Short enough to feel live, long enough that typing a colour does not send
 *  six requests. */
const PLAN_DEBOUNCE_MS = 320;

export function Studio({ runId }: { runId: string }) {
  const [spec, setSpec] = useState<Spec | null>(null);
  const [original, setOriginal] = useState<Spec | null>(null);
  const [options, setOptions] = useState<CatalogOptions | null>(null);
  const [catalog, setCatalog] = useState<CatalogKpi[]>([]);
  const [ai, setAi] = useState<AiStatus>({ available: false });
  const [tables, setTables] = useState<string[]>([]);
  const [plan, setPlan] = useState<PlanReport | null>(null);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stage, setStage] = useState<string>('source');
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => {
    Promise.all([getSpec(runId), getOptions(), listCatalogKpis(), getAiStatus(),
                 // A run that has not produced tables yet is not an error here;
                 // the editors that need them simply have nothing to target.
                 listTables(runId).catch(() => [])])
      .then(([loaded, opts, kpis, status, found]) => {
        setSpec(loaded);
        // A deep copy, so Revert restores what was on disk rather than a
        // reference the edits have already mutated.
        setOriginal(JSON.parse(JSON.stringify(loaded)));
        setOptions(opts);
        setCatalog(kpis.kpis);
        setAi(status);
        setTables(found.map((t) => t.name));
      })
      .catch((err: Error) => setError(err.message));
  }, [runId]);

  /* Every edit is written straight through to the server, which answers with
     the stages it invalidated. That round trip is the feature: the action bar
     can say "3 stages, ~4s" before the user commits to waiting. */
  function edit(next: Spec) {
    setSpec(next);
    setChecking(true);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      putSpec(runId, next)
        .then((report) => { setPlan(report); setError(null); })
        .catch((err: Error) => { setError(err.message); setPlan(null); })
        .finally(() => setChecking(false));
    }, PLAN_DEBOUNCE_MS) as unknown as number;
  }

  /* The planner writes straight to `spec.json` through `put_spec`, so the
     Studio's copy is stale the moment a patch lands. Re-read rather than
     merging locally: the server validated and may have normalised. */
  function reload() {
    getSpec(runId).then((loaded) => {
      setSpec(loaded);
      return putSpec(runId, loaded).then(setPlan);
    }).catch((err: Error) => setError(err.message));
  }

  async function rerun() {
    try {
      await rerunRun(runId);
      navigate(`/runs/${runId}`);
    } catch (err) {
      setError((err as Error).message);
    }
  }

  function revert() {
    if (original === null) return;
    edit(JSON.parse(JSON.stringify(original)));
  }

  if (error && spec === null) {
    return (
      <section class="view" id="view-studio">
        <p class="empty">{error}</p>
      </section>
    );
  }
  if (spec === null || options === null) {
    return (
      <section class="view" id="view-studio">
        <p class="empty">Loading…</p>
      </section>
    );
  }

  const shared = { spec, options, catalog, tables, runId, onChange: edit };
  const dirty = plan?.dirty ?? [];

  return (
    <section class="view" id="view-studio">
      <div class="view-head">
        <a class="back" href={`/runs/${runId}`}
           onClick={(e) => { e.preventDefault(); navigate(`/runs/${runId}`); }}>
          ← Results
        </a>
        <h1 id="studio-company">
          {String((spec['profile'] as Spec | undefined)?.['identity']?.['name'] ?? 'Studio')}
        </h1>
      </div>

      <div class="studio-body">
        <aside class="studio-rail" id="studio-rail">
          {STAGES.map(([id, label]) => (
            <button class={`rail-btn${stage === id ? ' active' : ''}`}
                    data-stage={id} key={id} onClick={() => setStage(id)}>
              {label}
            </button>
          ))}
        </aside>

        <div class="studio-panels">
          <div class="studio-panel active" data-panel={stage}>
            {stage === 'source' && <SourcePanel {...shared} />}
            {stage === 'clean' && <CleanPanel {...shared} />}
            {stage === 'model' && <ModelPanel {...shared} />}
            {stage === 'kpis' && <KpiPanel {...shared} />}
            {stage === 'analysis' && <AnalysisPanel {...shared} />}
            {stage === 'design' && <DesignPanel {...shared} />}
            {stage === 'outputs' && <OutputsPanel {...shared} />}
            {stage === 'ai' && <AiPanel {...shared} status={ai} runId={runId}
                                       onApplied={reload} />}
          </div>
        </div>
      </div>

      <div class="studio-bar" id="studio-bar">
        <div class="studio-bar-text" id="studio-plan">
          {error
            ? <span style={{ color: 'var(--critical)' }}>{error}</span>
            : checking
              ? 'Checking…'
              : plan === null
                ? 'No changes yet.'
                : dirty.length === 0
                  ? 'Everything is up to date.'
                  : (
                    <>
                      <strong>{dirty.length} stage{dirty.length > 1 ? 's' : ''}</strong>
                      {' '}to rebuild — about {plan.estimated_seconds}s.{' '}
                      <span style={{ color: 'var(--muted)' }}>{dirty.join(' · ')}</span>
                    </>
                  )}
        </div>
        <div>
          <button class="ghost" id="studio-revert" onClick={revert}>
            Revert changes
          </button>
          <button class="primary" id="studio-rerun" disabled={dirty.length === 0}
                  onClick={() => void rerun()}>
            Re-run
          </button>
        </div>
      </div>
    </section>
  );
}
