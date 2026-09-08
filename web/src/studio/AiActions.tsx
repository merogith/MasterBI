import { useState } from 'preact/hooks';
import {
  aiApply, aiEstimate, aiPlan, GOAL_MAX_CHARS,
  type AiEstimate as Estimate,
} from '../lib/api';
import { useOverlay } from '../lib/overlay';

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
  // What this reader wants out of the report. Before 6.2b there was no box:
  // the button took no input at all, so the only way to steer the planner was
  // to reject a patch and pay for another one.
  const [goal, setGoal] = useState('');
  const [estimatedFor, setEstimatedFor] = useState('');

  async function runEstimate() {
    setEstimating(true);
    setError(null);
    try {
      setEstimate(await aiEstimate(runId, goal));
      setEstimatedFor(goal);
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
      const result = await aiPlan(runId, goal);
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
  const overlay = useOverlay(() => setChanges(null));

  return (
    <>
      {/* The goal comes first, and reading the panel on screen is what moved
          it. It was below the Cost block, which prices the request the goal is
          part of — so the order was "press Estimate, scroll past the number,
          find the box, type, and now the number above is wrong". The stale
          notice still earns its place for someone who goes back and edits, but
          it should not be the common path. Say what you want, price it, ask. */}
      <h3 class="studio-sub" id="ai-goal-label">What is this report for?</h3>
      <p class="hint">Optional, and it steers both requests. Left empty, the
         planner works from the profile and the objective alone.</p>
      {/* Labelled by the heading rather than by a visually-hidden `<label>`.
          Adding a `.visually-hidden` class here would have been the defect
          this same screenshot just found — a class name emitted with no rule
          behind it — and the heading already says exactly what the field is. */}
      <textarea id="ai-goal" class="ai-goal" rows={3} maxLength={GOAL_MAX_CHARS}
                aria-labelledby="ai-goal-label"
                placeholder="This goes to the board next week and the question is whether we have the cash to keep hiring."
                value={goal}
                onInput={(event) =>
                  setGoal((event.currentTarget as HTMLTextAreaElement).value)} />
      <p class="hint" id="ai-goal-count">
        {goal.length > 0
          ? `${goal.length.toLocaleString()} of ${GOAL_MAX_CHARS.toLocaleString()}`
            + ' characters · priced with the request'
          : `Up to ${GOAL_MAX_CHARS.toLocaleString()} characters.`}
      </p>

      <h3 class="studio-sub">Cost</h3>
      <p class="hint" id="ai-estimate">
        {estimate
          ? <>Estimated at most <strong>{estimate.worst_case_tokens.toLocaleString()}</strong>
              {' '}tokens, about <strong>${estimate.worst_case_cost_usd.toFixed(2)}</strong>
              {' '}for both requests. Output is assumed at the maximum, so the
              real cost is usually well under this.</>
          : 'Press Estimate to price both requests before spending anything on them.'}
      </p>
      {/* The ceiling has been in the spec since the AI layer was written and
          bound nothing until 6.2b. It is worth saying out loud only when the
          worst case would cross it — a limit reported on every run that is
          nowhere near it is the "invisible through repetition" failure 5.3d
          found in the basis badge. */}
      {estimate && estimate.within_ceiling === false && (
        <p class="caution" id="ai-over-ceiling">
          That is over this run's ceiling of
          {' '}{(estimate.ceiling ?? 0).toLocaleString()} tokens, so the run
          will stop once it crosses it and the report will go out with fewer
          paragraphs. Raise the ceiling in the spec, or shorten the goal.
        </p>
      )}
      {/* Priced *with* the goal, so an estimate taken before the goal changed
          is an answer to a different question. Saying so beats quietly showing
          a number for a request nobody will send — which is the exact drift
          this item found one layer down, in the endpoint. */}
      {estimate && estimatedFor !== goal && (
        <p class="hint" id="ai-estimate-stale">
          The goal has changed since this was priced. Estimate again to include it.
        </p>
      )}
      <button class="ghost" id="ai-estimate-btn" disabled={estimating}
              onClick={() => void runEstimate()}>
        {estimating ? 'Estimating…' : 'Estimate'}
      </button>

      <h3 class="studio-sub">Let the AI configure this run</h3>
      <p class="hint">Proposes changes to the KPIs, sections, exhibits,
         detectors and outputs — never to the profile, and never the plan
         figures. You accept or reject each one before anything is written.</p>
      <button class="ghost" id="ai-plan-btn" disabled={thinking}
              onClick={() => void requestPlan()}>
        {thinking ? 'Thinking…' : 'Suggest changes'}
      </button>

      {error && <p class="warn">{error}</p>}

      {changes !== null && (
        <>
          <div class="modal-scrim" id="plan-scrim" onClick={() => setChanges(null)} />
          {/* **This modal had neither Escape nor focus**, which measuring the
              overlays found and the plan did not name — it listed the three
              that already had both and called the trap the only gap. A dialog
              that never receives focus is worse than one without a trap: a
              keyboard user's next Tab goes to whatever followed the button
              they pressed, behind a scrim, with no way back and nothing to
              press Escape on. */}
          <div class="modal" id="plan-modal" role="dialog" aria-modal="true"
               aria-label="Proposed changes" tabIndex={-1}
               ref={overlay.ref} onKeyDown={overlay.onKeyDown}>
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
