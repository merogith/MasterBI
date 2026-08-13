import { useState } from 'preact/hooks';
import { aiApply, aiEstimate, aiPlan, type AiEstimate as Estimate } from '../lib/api';

/* Cost first, then the plan.
 *
 * Both are deliberately manual. Estimating before spending is the whole point
 * of having the button, and the proposed patch reaches nothing until the user
 * ticks rows and presses Apply — the planner writes configuration, never a
 * number, and the reviewer sees each path before it lands.
 */
interface Change {
  path: string;
  value: unknown;
  before?: unknown;
  rationale?: string;
  ok?: boolean;
  rejected?: string;
}

const showValue = (value: unknown): string =>
  value === undefined || value === null
    ? '—' : typeof value === 'string' ? value : JSON.stringify(value);

export function AiActions({ runId, onApplied }: {
  runId: string;
  onApplied: () => void;
}) {
  const [estimate, setEstimate] = useState<Estimate | null>(null);
  const [estimating, setEstimating] = useState(false);
  const [thinking, setThinking] = useState(false);
  const [changes, setChanges] = useState<Change[] | null>(null);
  const [summary, setSummary] = useState('');
  const [chosen, setChosen] = useState<Set<number>>(new Set());
  const [error, setError] = useState<string | null>(null);

  async function runEstimate() {
    setEstimating(true);
    setError(null);
    try {
      setEstimate(await aiEstimate(runId));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setEstimating(false);
    }
  }

  async function requestPlan() {
    setThinking(true);
    setError(null);
    try {
      const result = await aiPlan(runId);
      const proposed = (result.changes ?? []) as Change[];
      setChanges(proposed);
      setSummary(result.summary ?? '');
      // Pre-tick the legal ones: the common case is accepting most of a good
      // patch, and starting from nothing ticked makes the reviewer do clerical
      // work before they can do the actual review.
      setChosen(new Set(proposed
        .map((change, index) => (change.ok === false ? -1 : index))
        .filter((index) => index >= 0)));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setThinking(false);
    }
  }

  async function apply() {
    if (changes === null) return;
    const picked = [...chosen].sort((a, b) => a - b)
      .map((index) => changes[index] as Change)
      .map((change) => ({ path: change.path, value: change.value }));
    try {
      await aiApply(runId, picked);
      setChanges(null);
      onApplied();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  const refused = (changes ?? []).filter((change) => change.ok === false).length;

  return (
    <>
      <h3 class="studio-sub">Cost</h3>
      <p class="hint" id="ai-estimate">
        {estimate
          ? <>Estimated at most <strong>{estimate.worst_case_tokens.toLocaleString()}</strong>
              {' '}tokens, about <strong>${estimate.worst_case_cost_usd.toFixed(2)}</strong>
              {' '}for both requests. Output is assumed at the ceiling, so the
              real cost is usually well under this.</>
          : 'Press Estimate to price both requests before spending anything on them.'}
      </p>
      <button class="ghost" id="ai-estimate-btn" disabled={estimating}
              onClick={() => void runEstimate()}>
        {estimating ? 'Estimating…' : 'Estimate'}
      </button>

      <h3 class="studio-sub">Let the AI configure this run</h3>
      <p class="hint">Proposes changes to the KPIs, sections, exhibits,
         detectors and outputs — never to the profile. You accept or reject each
         one before anything is written.</p>
      <button class="ghost" id="ai-plan-btn" disabled={thinking}
              onClick={() => void requestPlan()}>
        {thinking ? 'Thinking…' : 'Suggest changes'}
      </button>

      {error && <p class="warn">{error}</p>}

      {changes !== null && (
        <>
          <div class="modal-scrim" id="plan-scrim" onClick={() => setChanges(null)} />
          <div class="modal" id="plan-modal" role="dialog"
               aria-label="Proposed changes">
            <div class="modal-head">
              <h2>Proposed changes</h2>
              <button class="ghost" id="plan-close" aria-label="Close"
                      onClick={() => setChanges(null)}>✕</button>
            </div>
            <p class="hint" id="plan-summary">{summary}</p>

            <div id="plan-changes">
              {changes.length === 0
                ? <p class="hint">The planner proposed no changes.</p>
                : changes.map((change, index) => (
                  <div class={`plan-change ${change.ok === false ? 'plan-refused' : ''}`}
                       key={index}>
                    <label class="plan-pick">
                      <input type="checkbox" data-plan={index}
                             disabled={change.ok === false}
                             checked={chosen.has(index)}
                             onChange={() => {
                               const next = new Set(chosen);
                               if (next.has(index)) next.delete(index);
                               else next.add(index);
                               setChosen(next);
                             }} />
                      <code>{change.path}</code>
                    </label>
                    <div class="plan-diff">
                      <span class={`plan-before${
                        change.before === null || change.before === undefined
                          ? ' plan-unset' : ''}`}>
                        {showValue(change.before)}
                      </span>
                      <span class="plan-arrow">→</span>
                      <span class="plan-after">{showValue(change.value)}</span>
                    </div>
                    <p class="plan-why">{change.rationale ?? ''}</p>
                    {change.ok === false && (
                      <p class="plan-reject">Refused — {change.rejected}</p>
                    )}
                  </div>
                ))}
            </div>

            <div class="modal-foot">
              <span class="hint" id="plan-count">
                {chosen.size} of {changes.length} selected
                {refused > 0 && ` · ${refused} refused`}
              </span>
              <div>
                <button class="ghost" id="plan-cancel"
                        onClick={() => setChanges(null)}>Cancel</button>
                <button class="primary" id="plan-apply" disabled={chosen.size === 0}
                        onClick={() => void apply()}>
                  Apply {chosen.size}
                </button>
              </div>
            </div>
          </div>
        </>
      )}
    </>
  );
}
