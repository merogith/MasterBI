import { useState } from 'preact/hooks';
import { profileUpload, type Spec, type UploadProfile } from '../lib/api';
import { setPath } from '../lib/spec';

/* Upload, then adopt — two acts, deliberately.
 *
 * Profiling inspects the file and changes nothing. Adoption is what switches
 * the run's source to it and records the column mapping, and it is offered
 * only when the file's strongest shape is actually usable: a file whose
 * required fields did not map cannot drive a run, and a button that pretended
 * otherwise would fail somewhere much less obvious.
 */
export function AdoptUpload({ spec, onChange }: {
  spec: Spec;
  onChange: (spec: Spec) => void;
}) {
  const [data, setData] = useState<UploadProfile | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [adopted, setAdopted] = useState<string | null>(null);

  const best = data?.shapes?.[0];
  const stored = data?.stored_as;

  async function upload(file: File) {
    setBusy(true);
    setError(null);
    setAdopted(null);
    try {
      setData(await profileUpload(file));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function adopt() {
    if (!data || !best?.usable || !stored) return;
    let next = setPath(spec, 'source.kind', 'upload');
    next = setPath(next, 'source.uploads', [stored]);
    const mapping = ((next['model'] as Spec | undefined)?.['mapping'] ?? {}) as Spec;
    next = setPath(next, 'model.mapping', {
      ...mapping,
      [best.target_table]: Object.fromEntries(
        (best.matches ?? []).filter((match) => match.column)
          .map((match) => [match.field, match.column])),
    });
    onChange(next);
    setAdopted(data.filename);
  }

  return (
    <div class="upload-row">
      <label class="dropzone compact" id="studio-drop">
        <input type="file" id="studio-file" accept=".csv,.tsv,.xlsx,.xls" hidden
               onChange={(e) => {
                 const file = e.currentTarget.files?.[0];
                 if (file) void upload(file);
               }} />
        <span class="drop-title">Upload your own data</span>
        <span class="drop-sub">
          CSV, TSV or Excel — we read it, profile it and suggest the fixes
        </span>
      </label>

      <div id="studio-upload-result">
        {busy && <p class="hint">Reading and profiling…</p>}
        {error && <div class="warn-banner">{error}</div>}
        {adopted && (
          <div class="notice">Using <strong>{adopted}</strong> as this run's
             source. Its suggested fixes are offered in the Clean panel.</div>
        )}

        {data && !adopted && (
          <div class="upload-report">
            <div class="upload-head">
              <strong>{data.filename}</strong>
              {' — '}{(data.read?.rows ?? 0).toLocaleString()} rows,
              {' '}{(data.read?.columns ?? []).length} columns
            </div>
            {best && (
              <div class="upload-shape">
                Looks like a <strong>{best.shape.replace(/_/g, ' ')}</strong> →{' '}
                <code>{best.target_table}</code>
                {!best.usable && ` — but ${(best.missing_required ?? []).join(', ')} did not map`}
              </div>
            )}
            <button class="primary" id="use-upload" disabled={!best?.usable || !stored}
                    onClick={adopt}>
              Use this file
            </button>
            {!best?.usable && (
              <span class="hint">Required fields did not map, so this file cannot
                 drive a run yet.</span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
