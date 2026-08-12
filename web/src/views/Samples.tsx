import { useEffect, useState } from 'preact/hooks';
import { createRun, listSamples, type Sample } from '../lib/api';
import { navigate } from '../lib/router';

export function Samples() {
  const [samples, setSamples] = useState<Sample[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState<string | null>(null);

  useEffect(() => {
    listSamples().then(setSamples, (err: Error) => setError(err.message));
  }, []);

  async function start(id: string) {
    setStarting(id);
    try {
      const run = await createRun({ mode: 'sample', sample_id: id });
      // The run gets its own URL before it has any output, so a reload during a
      // four-minute render lands back on the running screen rather than home.
      navigate(`/runs/${run.run_id}`);
    } catch (err) {
      setError((err as Error).message);
      setStarting(null);
    }
  }

  return (
    <section class="view" id="view-samples">
      <div class="view-head">
        <a class="back" href="/" onClick={(e) => { e.preventDefault(); navigate('/'); }}>
          ← Back
        </a>
        <h1>Sample companies</h1>
        <p class="lede">Each one is generated from a profile with a deliberate
           story built in. Nothing here is a real business.</p>
      </div>

      <div class="sample-grid" id="sample-grid">
        {error && <p class="empty">Could not load samples: {error}</p>}
        {!error && samples === null && <p class="empty">Loading…</p>}
        {samples?.length === 0 && <p class="empty">No samples found.</p>}
        {samples?.map((sample) => (
          <article class="sample-card" key={sample.id}>
            <div>
              <h2>{sample.title}</h2>
              <div class="sample-tagline">{sample.tagline}</div>
            </div>
            <p class="sample-story">{sample.story}</p>
            <div class="tag-row">
              {(sample.tags ?? []).map((tag) => <span class="tag" key={tag}>{tag}</span>)}
            </div>
            <p class="watch-for"><strong>Watch for:</strong> {sample.watch_for ?? ''}</p>
            <button class="primary" data-sample={sample.id}
                    disabled={starting !== null}
                    onClick={() => start(sample.id)}>
              {starting === sample.id ? 'Starting…' : 'Generate this pack'}
            </button>
          </article>
        ))}
      </div>
    </section>
  );
}
