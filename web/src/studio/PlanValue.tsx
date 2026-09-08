/* An editable proposed value.
 *
 * The review modal could accept or reject a change and nothing else, so a
 * reviewer who agreed with the reasoning and not with the value had one move:
 * reject it, and pay for another request. "The user sees and can edit every
 * change the AI made" was half true.
 *
 * **Typed on the proposed value, never on the current one.** Measured across a
 * real spec: of 51 patchable paths, **17 currently hold `null`** — an unset
 * generator knob, an empty override, a section list the run never customised —
 * which is why the diff has a `plan-unset` class in the first place. Typing off
 * `before` would leave a third of the patch surface uneditable, and the third
 * most likely to be proposed, since a planner mostly fills things in.
 *
 * **No raw JSON box.** Phase 1.1 names "a Studio that asks business users to
 * type raw JSON" as one of the product's defects, and re-introducing it on the
 * one screen a person reads to decide whether to trust a model would be a poor
 * trade. So the four shapes that actually occur get real controls, and anything
 * nested is shown as it is and marked as not editable here — an honest "no"
 * rather than a text box that turns a review into a syntax exercise.
 */
import type { JSX } from 'preact';

export type Editable = 'bool' | 'number' | 'string' | 'list' | 'opaque';

export function shapeOf(value: unknown): Editable {
  if (typeof value === 'boolean') return 'bool';
  if (typeof value === 'number') return 'number';
  if (typeof value === 'string') return 'string';
  // A list of scalars — section ids, KPI ids, artifact names. Every list the
  // planner can legally propose is one of these; a list of objects is not
  // reachable through `PATCHABLE_SECTIONS` and would be `opaque` if it were.
  if (Array.isArray(value)
      && value.every((item) => typeof item === 'string'
                            || typeof item === 'number')) return 'list';
  return 'opaque';
}

export const showValue = (value: unknown): string =>
  value === undefined || value === null
    ? '—' : typeof value === 'string' ? value : JSON.stringify(value);

export function PlanValue({ value, index, onChange }: {
  value: unknown;
  index: number;
  onChange: (next: unknown) => void;
}): JSX.Element {
  const shape = shapeOf(value);

  if (shape === 'bool') {
    return (
      <label class="plan-edit plan-edit-bool">
        <input type="checkbox" data-plan-value={index} checked={value as boolean}
               onChange={(event) =>
                 onChange((event.currentTarget as HTMLInputElement).checked)} />
        <span>{String(value)}</span>
      </label>
    );
  }

  if (shape === 'number') {
    return (
      <input class="plan-edit" type="number" data-plan-value={index}
             value={String(value)}
             onInput={(event) => {
               const raw = (event.currentTarget as HTMLInputElement).value;
               // An empty box is mid-edit, not a proposal of zero. Leaving the
               // old value in place keeps the change legal while somebody is
               // still typing, rather than flashing a rejection at them.
               if (raw !== '') onChange(Number(raw));
             }} />
    );
  }

  if (shape === 'string') {
    return (
      <input class="plan-edit" type="text" data-plan-value={index}
             value={value as string}
             onInput={(event) =>
               onChange((event.currentTarget as HTMLInputElement).value)} />
    );
  }

  if (shape === 'list') {
    const items = value as (string | number)[];
    return (
      <span class="plan-edit plan-edit-list" data-plan-value={index}>
        {items.length === 0 && <span class="plan-unset">nothing</span>}
        {items.map((item, at) => (
          <span class="plan-chip" key={`${item}-${at}`}>
            {String(item)}
            {/* Removing is the edit a reviewer actually makes — "keep this
                change but not that id". Adding one needs the catalog the
                planner was given, which is a picker rather than a chip, and
                belongs with the KPI panel that already has one. */}
            <button type="button" class="plan-chip-x" data-plan-drop={`${index}:${at}`}
                    aria-label={`Remove ${item}`}
                    onClick={() => onChange(items.filter((_, i) => i !== at))}>
              ✕
            </button>
          </span>
        ))}
      </span>
    );
  }

  return (
    <span class="plan-edit plan-edit-opaque" data-plan-value={index}>
      {showValue(value)}
      <span class="plan-opaque-note">not editable here</span>
    </span>
  );
}
