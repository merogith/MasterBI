import { useState } from 'preact/hooks';
import { profileUpload, type UploadProfile } from '../lib/api';
import { href, navigate } from '../lib/router';

/* Bring your data.
 *
 * This screen inspects a file and changes nothing — profiling is not adoption.
 * A run needs a company profile before it can read anyone's numbers, so the
 * honest order is survey first, then adopt the file in the Studio's Source
 * panel. 2.3 makes that one flow instead of two; until it does, the screen
 * says so rather than implying an upload is enough.
 */
function confidentCount(matches: { status?: string }[]): number {
  return matches.filter((m) => m.status === 'confident').length;
}

export function Builder() {
  const [data, setData] = useState<UploadProfile | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);

  async function upload(file: File) {
    setBusy(true);
    setError(null);
    setData(null);
    try {
      setData(await profileUpload(file));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const columns = data?.profile?.columns ?? [];
  const notes = data?.read?.notes ?? [];
  const problems = data?.profile?.problems ?? [];
  // Ordered by how many fields matched *confidently*, not by the server's
  // order. `detect_shape` ranks by ratio, so a five-field shape with one match
  // outranks an eight-field shape with two — which puts `crm_deals` above
  // `pnl_export` for a literal P&L export. Fixing that ranking is mapping work;
  // showing the strongest candidate first costs nothing and is true either way.
  const shapes = [...(data?.shapes ?? [])].sort(
    (a, b) => confidentCount(b.matches ?? []) - confidentCount(a.matches ?? []));

  return (
    <section class="view" id="view-builder">
      <div class="view-head">
        <a class="back" href={href('/')} onClick={(e) => { e.preventDefault(); navigate('/'); }}>
          ← Back
        </a>
        <h1>Bring your data</h1>
        <p class="lede">Upload a CSV or Excel file. We read it the way it was
           actually written — encoding, delimiter, a title block above the real
           header — then profile every column and work out which fact table it
           could become.</p>
      </div>

      <div class="notice">
        <strong>What happens next.</strong> This step inspects your file and
        changes nothing. To run it, a company profile has to exist first, so the
        order is{' '}
        <button class="linkish" data-nav="survey"
                onClick={() => navigate('/survey')}>Build your own</button>{' '}
        to answer the profile questions, then adopt this file as the data source
        in the Studio's <em>Source</em> panel. Making that one flow instead of
        two is the next thing being built.
      </div>

      <label class={`dropzone${dragging ? ' dragging' : ''}`} id="dropzone"
             onDragEnter={(e) => { e.preventDefault(); setDragging(true); }}
             onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
             onDragLeave={(e) => { e.preventDefault(); setDragging(false); }}
             onDrop={(e) => {
               e.preventDefault();
               setDragging(false);
               const file = e.dataTransfer?.files?.[0];
               if (file) void upload(file);
             }}>
        <input type="file" id="file-input" accept=".csv,.tsv,.xlsx,.xls" hidden
               onChange={(e) => {
                 const file = e.currentTarget.files?.[0];
                 if (file) void upload(file);
               }} />
        <span class="drop-icon" aria-hidden="true">⬆</span>
        <span class="drop-title">Drop a spreadsheet here, or click to choose</span>
        <span class="drop-sub">CSV, TSV or Excel</span>
      </label>

      <div id="upload-result" hidden={!busy && !error && data === null}>
        {busy && <p class="empty">Profiling columns…</p>}
        {error && <p class="empty">Upload failed: {error}</p>}

        {data && (
          <>
            <h2 class="section-title" style={{ marginTop: '28px' }}>{data.filename}</h2>
            <p class="section-sub">
              {(data.profile?.rows ?? 0).toLocaleString()} rows · {columns.length} columns
            </p>

            {notes.length > 0 && (
              <div class="notice">
                <strong>How this file was read.</strong>
                <ul>{notes.map((note, i) => <li key={i}>{note}</li>)}</ul>
              </div>
            )}

            {shapes.length > 0 && (
              <div class="notice">
                <strong>What this could become.</strong>
                <ul>
                  {shapes.map((shape) => {
                    const matches = shape.matches ?? [];
                    const sure = confidentCount(matches);
                    const missing = (shape.missing_required ?? []).length;
                    return (
                      <li key={shape.shape}>
                        <strong>{shape.shape}</strong> → <code>{shape.target_table}</code>
                        {` — ${sure} of ${matches.length} field`}
                        {matches.length === 1 ? '' : 's'} matched confidently
                        {missing > 0 && `, ${missing} required field${
                          missing === 1 ? '' : 's'} missing`}
                        {!shape.usable && <em> (not usable as-is)</em>}
                      </li>
                    );
                  })}
                </ul>
                <p class="watch-for">A low match count means the column names did
                   not resemble that shape — not that your data is wrong.
                   Correcting a mapping by hand is part of the flow being built
                   next.</p>
              </div>
            )}

            {problems.length > 0 && (
              <div class="notice">
                <strong>Worth a look.</strong>
                <ul>{problems.map((problem, i) => <li key={i}>{problem}</li>)}</ul>
              </div>
            )}

            <div class="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Column</th><th>Reads as</th><th class="num">Non-null</th>
                    <th class="num">Missing</th><th class="num">Unique</th>
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
                      <td class="num">{(column.non_null ?? 0).toLocaleString()}</td>
                      <td class="num">{((column.null_pct ?? 0) * 100).toFixed(1)}%</td>
                      <td class="num">{(column.unique ?? 0).toLocaleString()}</td>
                      <td>{(column.samples ?? []).join(' · ')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div class="surprise-row" style={{ marginTop: '24px' }}>
              <div>
                <h3>Ready to use this file?</h3>
                <p>Answer the profile questions first — a run needs to know who
                   the company is before it can read its numbers. Then adopt this
                   file in the Studio's Source panel.</p>
              </div>
              <button class="primary" onClick={() => navigate('/survey')}>
                Build the profile
              </button>
            </div>
          </>
        )}
      </div>
    </section>
  );
}
