import { useEffect, useState } from 'preact/hooks';
import { api, validateFormula, type Spec } from '../lib/api';
import { setPath, titleCase } from '../lib/spec';

/* The three "add something" flows.
 *
 * Each validates before it writes. A cleaning step with malformed parameters,
 * a calculated column whose formula does not parse, or a KPI referencing a
 * column that does not exist would all fail much later — inside a run, where
 * the message is furthest from the edit that caused it.
 */

export function AddCleaningStep({ spec, ops, tables, onChange }: {
  spec: Spec;
  ops: { name: string; label: string; help: string }[];
  tables: string[];
  onChange: (spec: Spec) => void;
}) {
  const [op, setOp] = useState('');
  const [table, setTable] = useState('');
  const [raw, setRaw] = useState('');
  const [error, setError] = useState<string | null>(null);

  const help = ops.find((candidate) => candidate.name === op)?.help ?? '';

  function add() {
    if (!op) { setError('Choose an operation.'); return; }
    let params: unknown = {};
    if (raw.trim()) {
      try {
        params = JSON.parse(raw);
      } catch (err) {
        setError('Parameters must be valid JSON — ' + (err as Error).message);
        return;
      }
    }
    const steps = ((spec['cleaning'] as Spec | undefined)?.['steps'] ?? []) as Spec[];
    onChange(setPath(spec, 'cleaning.steps',
                     [...steps, { op, table: table || null, params, enabled: true }]));
    setRaw('');
    setError(null);
  }

  return (
    <>
      <div class="field-row" style={{ marginTop: '16px' }}>
        <label class="field">
          <span>Add a step</span>
          <select id="op-pick" value={op} onChange={(e) => setOp(e.currentTarget.value)}>
            <option value="">Choose an operation…</option>
            {ops.map((candidate) => (
              <option value={candidate.name} key={candidate.name}>{candidate.label}</option>
            ))}
          </select>
        </label>
        <label class="field">
          <span>Table</span>
          <select id="op-table" value={table}
                  onChange={(e) => setTable(e.currentTarget.value)}>
            <option value="">Every table</option>
            {tables.map((name) => (
              <option value={name} key={name}>{titleCase(name)}</option>
            ))}
          </select>
        </label>
        <label class="field" style={{ flex: 1, minWidth: '220px' }}>
          <span>Parameters (JSON)</span>
          <input id="op-params" value={raw} placeholder='{"column": "revenue", "to": "number"}'
                 onInput={(e) => setRaw(e.currentTarget.value)} />
        </label>
        <button class="ghost" id="op-add" style={{ alignSelf: 'flex-end' }}
                onClick={add}>Add step</button>
      </div>
      {error && <div class="fx-status err" id="op-status">{error}</div>}
      {help && <div class="hint" id="op-help" style={{ marginTop: '8px' }}>{help}</div>}
    </>
  );
}

export function AddCalculatedColumn({ spec, runId, tables, onChange }: {
  spec: Spec;
  runId: string;
  tables: string[];
  onChange: (spec: Spec) => void;
}) {
  const [table, setTable] = useState(tables[0] ?? '');
  const [name, setName] = useState('');
  const [expression, setExpression] = useState('');
  const [status, setStatus] = useState<{ ok: boolean; message: string } | null>(null);

  useEffect(() => {
    if (!table && tables[0]) setTable(tables[0]);
  }, [tables, table]);

  async function add() {
    if (!name.trim() || !expression.trim()) {
      setStatus({ ok: false, message: 'Name and formula are both needed.' });
      return;
    }
    // Row scope, against this run's own tables: a column that references a
    // field the table does not have has to fail here, not inside the run.
    const check = await validateFormula({
      expression: expression.trim(), scope: 'row', run_id: runId, table,
    });
    if (!check.ok) {
      setStatus({
        ok: false,
        message: check.error?.message
          ?? `Unknown: ${(check.unknown ?? []).join(', ')}`,
      });
      return;
    }
    const columns = ((spec['model'] as Spec | undefined)?.['calculated_columns'] ?? []) as Spec[];
    onChange(setPath(spec, 'model.calculated_columns', [...columns, {
      table, name: name.trim(), expression: expression.trim(), description: '',
    }]));
    setName('');
    setExpression('');
    setStatus(null);
  }

  return (
    <>
      <div class="field-row" style={{ marginTop: '16px' }}>
        <label class="field">
          <span>Table</span>
          <select id="col-table" value={table}
                  onChange={(e) => setTable(e.currentTarget.value)}>
            {tables.map((candidate) => (
              <option value={candidate} key={candidate}>{titleCase(candidate)}</option>
            ))}
          </select>
        </label>
        <label class="field">
          <span>Column name</span>
          <input id="col-name" placeholder="acv_delta" maxLength={40} value={name}
                 onInput={(e) => setName(e.currentTarget.value)} />
        </label>
        <label class="field" style={{ flex: 1, minWidth: '260px' }}>
          <span>Formula</span>
          <input id="col-expr" placeholder="final_acv - initial_acv" value={expression}
                 onInput={(e) => setExpression(e.currentTarget.value)} />
        </label>
        <button class="ghost" id="col-add" style={{ alignSelf: 'flex-end' }}
                onClick={() => void add()}>Add column</button>
      </div>
      {status && (
        <div class={`fx-status ${status.ok ? 'ok' : 'err'}`} id="col-status">
          {status.message}
        </div>
      )}
    </>
  );
}

const UNITS = ['currency', 'pct', 'count', 'months', 'days', 'hours', 'score', 'ratio'];
const DIRECTIONS = ['higher_is_better', 'lower_is_better'];
const PERSPECTIVES = ['financial', 'customer', 'process', 'learning'];
const TIMINGS = ['leading', 'lagging'];

export function AddKpi({ spec, runId, onChange, onClose }: {
  spec: Spec;
  runId: string;
  onChange: (spec: Spec) => void;
  onClose: () => void;
}) {
  const [name, setName] = useState('');
  const [expression, setExpression] = useState('');
  const [unit, setUnit] = useState('currency');
  const [direction, setDirection] = useState(DIRECTIONS[0] as string);
  const [perspective, setPerspective] = useState(PERSPECTIVES[0] as string);
  const [timing, setTiming] = useState(TIMINGS[1] as string);
  const [persist, setPersist] = useState(false);
  const [status, setStatus] = useState<{ ok: boolean; message: string } | null>(null);
  const [valid, setValid] = useState(false);

  useEffect(() => {
    if (!expression.trim()) { setStatus(null); setValid(false); return; }
    const timer = setTimeout(() => {
      validateFormula({ expression: expression.trim(), scope: 'series', run_id: runId })
        .then((check) => {
          if (check.ok) {
            setStatus({ ok: true, message: 'Formula checks out.' });
            setValid(true);
          } else {
            setStatus({
              ok: false,
              message: check.error?.message
                ?? `Unknown name: ${(check.unknown ?? []).join(', ')}`,
            });
            setValid(false);
          }
        })
        .catch((err: Error) => {
          setStatus({ ok: false, message: err.message });
          setValid(false);
        });
    }, 340);
    return () => clearTimeout(timer);
  }, [expression, runId]);

  async function add() {
    const label = name.trim() || 'Custom KPI';
    try {
      // Persisting is optional: a KPI for this run alone lives in the spec, one
      // kept for later is written to the user library and offered on every run.
      if (persist) {
        await api('/api/catalog/kpis', {
          method: 'POST',
          body: JSON.stringify({
            name: label, expression: expression.trim(), unit, direction,
            perspective, timing,
          }),
        });
      }
      const id = label.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '');
      const custom = ((spec['metrics'] as Spec | undefined)?.['custom'] ?? []) as Spec[];
      onChange(setPath(spec, 'metrics.custom', [...custom, {
        id, name: label, perspective, tier: 2, timing, direction,
        formula: expression.trim(), unit, owner_role: 'Unassigned',
        compute: { kind: 'formula', expression: expression.trim() },
        origin: 'user',
      }]));
      onClose();
    } catch (err) {
      setStatus({ ok: false, message: (err as Error).message });
    }
  }

  return (
    <>
      <div class="modal-scrim" id="fx-scrim" onClick={onClose} />
      <div class="modal" id="fx-modal" role="dialog" aria-label="Add a KPI">
        <div class="modal-head">
          <h2 id="fx-title">Add a KPI</h2>
          <button class="ghost" id="fx-close" aria-label="Close" onClick={onClose}>✕</button>
        </div>

        <div class="field-row">
          <label class="field"><span>Name</span>
            <input id="fx-name" placeholder="e.g. Cost per Lead" maxLength={60}
                   value={name} onInput={(e) => setName(e.currentTarget.value)} />
          </label>
          <label class="field"><span>Unit</span>
            <select id="fx-unit" value={unit}
                    onChange={(e) => setUnit(e.currentTarget.value)}>
              {UNITS.map((option) => <option value={option} key={option}>{option}</option>)}
            </select>
          </label>
          <label class="field"><span>Direction</span>
            <select id="fx-direction" value={direction}
                    onChange={(e) => setDirection(e.currentTarget.value)}>
              {DIRECTIONS.map((option) => (
                <option value={option} key={option}>{titleCase(option)}</option>
              ))}
            </select>
          </label>
        </div>

        <label class="field"><span>Formula</span>
          <textarea id="fx-expression" rows={3} spellcheck={false} value={expression}
                    onInput={(e) => setExpression(e.currentTarget.value)} />
        </label>
        {status && (
          <div class={`fx-status ${status.ok ? 'ok' : 'err'}`} id="fx-status">
            {status.message}
          </div>
        )}

        <div class="field-row">
          <label class="field"><span>Perspective</span>
            <select id="fx-perspective" value={perspective}
                    onChange={(e) => setPerspective(e.currentTarget.value)}>
              {PERSPECTIVES.map((option) => (
                <option value={option} key={option}>{titleCase(option)}</option>
              ))}
            </select>
          </label>
          <label class="field"><span>Timing</span>
            <select id="fx-timing" value={timing}
                    onChange={(e) => setTiming(e.currentTarget.value)}>
              {TIMINGS.map((option) => (
                <option value={option} key={option}>{titleCase(option)}</option>
              ))}
            </select>
          </label>
        </div>

        <label class="toggle">
          <input type="checkbox" id="fx-persist" checked={persist}
                 onChange={() => setPersist(!persist)} />
          <span>Keep this KPI for future runs
            <span class="toggle-sub">writes it to your library, not just this run</span>
          </span>
        </label>

        <div class="modal-foot">
          <button class="ghost" id="fx-cancel" onClick={onClose}>Cancel</button>
          <button class="primary" id="fx-add" disabled={!valid}
                  onClick={() => void add()}>Add KPI</button>
        </div>
      </div>
    </>
  );
}
