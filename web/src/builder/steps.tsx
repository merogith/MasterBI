import type {
  QualityReport, SurveyQuestion, UploadColumn, UploadPlan, UploadProfile,
} from '../lib/api';

/* The three screens between a dropped file and a running pipeline.
 *
 * Each is a pure view over what the server said. The funnel's state lives in
 * `Builder.tsx`; nothing here fetches, so a step can be read on its own and
 * the order they run in is stated in one place rather than implied by five.
 */

/** Confidence as a bar, because "0.83" means nothing and a filled bar does.
 *  Three bands, matching `mapping.py`'s own CONFIDENT / PLAUSIBLE thresholds —
 *  restating them with different numbers here would let the colour disagree
 *  with the word beside it. */
function Confidence({ value }: { value: number }) {
  const band = value >= 0.75 ? 'sure' : value >= 0.45 ? 'maybe' : 'weak';
  return (
    <span class={`confidence ${band}`} title={`${Math.round(value * 100)}% confident`}>
      <span class="confidence-fill" style={{ width: `${Math.round(value * 100)}%` }} />
    </span>
  );
}

export function ReadStep({ data, plan, mapping, onMap }: {
  data: UploadProfile;
  plan: UploadPlan;
  mapping: Record<string, string>;
  onMap: (field: string, column: string) => void;
}) {
  const columns: UploadColumn[] = data.profile?.columns ?? [];
  const notes = data.read?.notes ?? [];
  const shape = (data.shapes ?? []).find((s) => s.shape === plan.shape);
  const fields = shape?.matches ?? Object.keys(plan.mapping).map(
    (field) => ({ field, column: plan.mapping[field] ?? null, confidence: 1 }));

  return (
    <>
      <div class="notice">
        <strong>We read this as {plan.table.replace(/_/g, ' ')}.</strong>{' '}
        {plan.note}. Every field below is editable — the match is a proposal,
        and you know your own export better than a name-similarity score does.
      </div>

      {(notes.length > 0 || plan.read_fixes.length > 0) && (
        <div class="notice">
          <strong>How the file was read.</strong>
          <ul>
            {notes.map((note, i) => <li key={`n${i}`}>{note}</li>)}
            {plan.read_fixes.map((fix, i) => <li key={`f${i}`}>{fix}</li>)}
          </ul>
        </div>
      )}

      <h2 class="section-title">Field mapping</h2>
      <p class="section-sub">
        Which of your columns becomes which field in <code>{plan.table}</code>.
      </p>
      <div class="table-wrap">
        <table class="mapping-table">
          <thead>
            <tr>
              <th>Field</th><th>Your column</th><th>Confidence</th><th>Why</th>
            </tr>
          </thead>
          <tbody>
            {fields.map((match) => {
              const chosen = mapping[match.field] ?? match.column ?? '';
              const required = 'required' in match
                ? (match as { required?: boolean }).required : false;
              return (
                <tr key={match.field}>
                  <td>
                    <strong>{match.field}</strong>
                    {required && <span class="required-pill">required</span>}
                  </td>
                  <td>
                    <select class="map-select" value={chosen}
                            aria-label={`Column for ${match.field}`}
                            onChange={(e) => onMap(match.field, e.currentTarget.value)}>
                      <option value="">— not supplied —</option>
                      {columns.map((column) => (
                        <option key={column.name} value={column.name}>{column.name}</option>
                      ))}
                    </select>
                  </td>
                  <td>
                    {chosen === (match.column ?? '')
                      ? <Confidence value={(match as { confidence?: number }).confidence ?? 0} />
                      : <span class="confidence-yours">yours</span>}
                  </td>
                  <td class="reason-cell">
                    {chosen === (match.column ?? '')
                      ? (match as { reason?: string }).reason ?? ''
                      : 'you chose this column'}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <details class="column-detail">
        <summary>All {columns.length} columns as they were read</summary>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Column</th><th>Reads as</th><th class="num">Missing</th>
                <th>Sample values</th>
              </tr>
            </thead>
            <tbody>
              {columns.map((column) => (
                <tr key={column.name}>
                  <td>
                    <strong>{column.name}</strong>
                    {(column.problems ?? []).length > 0 && (
                      <><br /><span class="watch-for">
                        {(column.problems ?? []).join(' · ')}
                      </span></>
                    )}
                  </td>
                  <td>{column.semantic ?? column.dtype ?? ''}</td>
                  <td class="num">{((column.null_pct ?? 0) * 100).toFixed(1)}%</td>
                  <td>{(column.samples ?? []).join(' · ')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </>
  );
}

export function QualityStep({ report, fill, onFill }: {
  report: QualityReport;
  fill: string[];
  onFill: (table: string, on: boolean) => void;
}) {
  const missing = report.tables_missing;
  const worthwhile = missing.filter((m) => (m.unlocks_kpis ?? 0) > 0);
  const quiet = missing.filter((m) => !(m.unlocks_kpis ?? 0));

  return (
    <>
      <div class={report.can_run ? 'notice' : 'warn-banner'}>
        <strong>
          {report.kpis_available} KPI{report.kpis_available === 1 ? '' : 's'} from
          what you supplied
        </strong>
        {report.kpis_blocked > 0 && `, ${report.kpis_blocked} waiting on data you have not sent.`}
        {report.kpis_blocked === 0 && '.'}
        {' '}This is what the dashboard will contain. A narrow board pack is the
        honest answer to a partial upload — the alternative is padding it with
        invention.
      </div>

      {report.blocking.length > 0 && (
        <div class="warn-banner">
          <strong>This cannot run yet.</strong>
          <ul>{report.blocking.map((b, i) => <li key={i}>{b}</li>)}</ul>
        </div>
      )}

      <h2 class="section-title">What you sent</h2>
      <ul class="table-list">
        {report.tables_present.map((table) => (
          <li key={table}><code>{table}</code> <span class="ok-pill">measured</span></li>
        ))}
      </ul>

      {worthwhile.length > 0 && (
        <>
          <h2 class="section-title">What else would help</h2>
          <p class="section-sub">
            Tick a table to have it modelled instead. Modelled figures are
            marked as such on every KPI that reads them, and in the report
            appendix — they are never presented as measured.
          </p>
          <div class="table-wrap">
            <table>
              <thead>
                <tr><th>Table</th><th class="num">Unlocks</th><th>For example</th>
                  <th>Send it, or model it</th></tr>
              </thead>
              <tbody>
                {worthwhile.map((entry) => (
                  <tr key={entry.table}>
                    <td>
                      <strong>{entry.table.replace(/_/g, ' ')}</strong>
                      {entry.supply_by && <><br />
                        <span class="watch-for">from a {entry.supply_by.toLowerCase()}</span>
                      </>}
                    </td>
                    <td class="num">{entry.unlocks_kpis}</td>
                    <td class="reason-cell">{entry.example_kpis.join(', ')}</td>
                    <td>
                      <label class="fill-toggle">
                        <input type="checkbox" checked={fill.includes(entry.table)}
                               onChange={(e) => onFill(entry.table, e.currentTarget.checked)} />
                        <span>model this one</span>
                      </label>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {quiet.length > 0 && (
        <p class="watch-for">
          {quiet.map((q) => q.table).join(', ')} would add nothing this scorecard
          asks for, so there is no reason to go and find them.
        </p>
      )}

      {report.schema_problems.length > 0 && (
        <details class="column-detail">
          <summary>{report.schema_problems.length} thing(s) the contract
            noticed</summary>
          <ul>{report.schema_problems.map((p, i) => <li key={i}>{p}</li>)}</ul>
        </details>
      )}
    </>
  );
}

export function QuestionsStep({ questions, answers, onAnswer, derivedNotes, name, onName }: {
  questions: SurveyQuestion[];
  answers: Record<string, string>;
  onAnswer: (id: string, value: string) => void;
  derivedNotes: string[];
  name: string;
  onName: (value: string) => void;
}) {
  return (
    <>
      {derivedNotes.length > 0 && (
        <div class="notice">
          <strong>Read from your file, so we are not asking.</strong>
          <ul>{derivedNotes.map((note, i) => <li key={i}>{note}</li>)}</ul>
        </div>
      )}

      <div class="question">
        <div class="question-text">What should we call the company?</div>
        <div class="question-help">Optional — we'll invent a name if you leave
          this blank.</div>
        <input class="name-input" maxLength={60} value={name}
               placeholder="e.g. Wayfarer Freight"
               onInput={(e) => onName(e.currentTarget.value)} />
      </div>

      {questions.map((question) => (
        <div class="question" data-qid={question.id} key={question.id}>
          <div class="question-text">{question.text}</div>
          <div class="question-help">{question.help}</div>
          <div class="options">
            {question.options.map((option) => (
              <label key={option.value}
                     class={['option', option.disabled ? 'disabled' : '',
                             option.approximate ? 'approximate' : '',
                             answers[question.id] === option.value ? 'selected' : '']
                       .filter(Boolean).join(' ')}>
                <input type="radio" name={question.id} value={option.value}
                       disabled={option.disabled}
                       checked={answers[question.id] === option.value}
                       onChange={() => onAnswer(question.id, option.value)} />
                <span>{option.label}</span>
                {option.note && <span class="option-note">{option.note}</span>}
              </label>
            ))}
          </div>
        </div>
      ))}
    </>
  );
}
