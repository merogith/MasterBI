import { useEffect, useState } from 'preact/hooks';
import { DemoNotice } from '../components/DemoNotice';
import { Failed, Loading } from '../components/State';
import { createRun, listSamples, type Sample } from '../lib/api';
import { navigate } from '../lib/router';

/* One milestone above the fold: see a finished board pack.
 *
 * This screen used to open with three equally-weighted doors and a paragraph
 * each. Three equal choices is not a choice — it is a decision handed to
 * someone who has not yet seen what the product makes, and the two doors that
 * are not "try a sample" both cost minutes before anything appears.
 *
 * So: one primary action that reaches a finished pack in about ten seconds, a
 * strip of the real companies it can build from — actual figures out of the
 * sample profiles, not marketing copy — and the other two doors below, still
 * complete, still one click, just no longer competing with the thing that
 * demonstrates the product.
 */
/** "$12M". The symbol comes from `Intl` so it matches the currency the pack is
 *  actually denominated in, rather than assuming everyone is American. */
function money(value: number, currency: string): string {
  const millions = value / 1_000_000;
  const digits = millions >= 10 ? 0 : 1;
  try {
    const symbol = new Intl.NumberFormat(undefined, {
      style: 'currency', currency, maximumFractionDigits: 0,
    }).format(0).replace(/[\d\s.,]/g, '');
    return `${symbol}${millions.toFixed(digits)}M`;
  } catch {
    return `${millions.toFixed(digits)}M ${currency}`;
  }
}

function stats(sample: Sample): string {
  return [
    sample.revenue ? money(sample.revenue, sample.currency ?? 'USD') : null,
    sample.headcount ? `${sample.headcount} people` : null,
    sample.customers ? `${sample.customers.toLocaleString()} customers` : null,
  ].filter(Boolean).join(' · ');
}

export function Home() {
  const [samples, setSamples] = useState<Sample[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);

  useEffect(() => {
    let live = true;
    listSamples().then(
      (list) => { if (live) setSamples(list); },
      (err: Error) => { if (live) setError(err.message); });
    return () => { live = false; };
  }, []);

  async function run(mode: 'sample' | 'surprise', sampleId?: string) {
    setStarting(true);
    setError(null);
    try {
      const created = await createRun(
        mode === 'sample'
          ? { mode, sample_id: sampleId }
          : { mode, seed: Math.floor(Math.random() * 1e7) });
      navigate(`/runs/${created.run_id}`);
    } catch (err) {
      setError((err as Error).message);
      setStarting(false);
    }
  }

  const featured = samples?.[0];

  return (
    <section class="view" id="view-home">
      <div class="hero">
        <p class="eyebrow">Performance reporting, generated</p>
        <h1>Turn a business into a board pack.</h1>
        <p class="lede">
          The right KPIs for a company's sector, size and objective — then the
          dashboard, the report, the deck and the data behind them. Every number
          is computed, not guessed.
        </p>
        <div class="hero-actions">
          <button class="primary big" id="btn-activate" disabled={starting || !featured}
                  onClick={() => featured && void run('sample', featured.id)}>
            {starting ? 'Building it…' : 'See a finished board pack'}
          </button>
          <span class="hero-note">
            {featured
              ? `Builds ${featured.title} — nine artifacts, about ten seconds.`
              : 'Loading the sample companies…'}
          </span>
        </div>
      </div>

      <DemoNotice />

      {error && <Failed message={error} onRetry={() => window.location.reload()} />}

      {/* Real companies with real figures, read off the sample profiles by the
          server. Showing what the product works on beats describing it, and
          deriving the numbers means this strip cannot drift from the packs it
          promises. */}
      {samples === null && !error && <Loading label="Loading the sample companies…" />}
      {samples && samples.length > 0 && (
        <div class="proof-strip" id="home-proof">
          {samples.slice(0, 4).map((sample) => (
            <button class="proof-card" key={sample.id} data-sample={sample.id}
                    disabled={starting}
                    onClick={() => void run('sample', sample.id)}>
              <span class="proof-title">{sample.title}</span>
              <span class="proof-tagline">{sample.tagline}</span>
              <span class="proof-stats">{stats(sample)}</span>
            </button>
          ))}
        </div>
      )}

      <h2 class="section-title" id="home-other-doors">Or start from your own business</h2>

      <div class="mode-grid">
        <button class="mode-card" data-nav="survey"
                onClick={() => navigate('/survey')}>
          <span class="mode-num">01</span>
          <h2>Build your own</h2>
          <p>Answer a short set of questions about your business — fewer if your
             sector makes some of them irrelevant. Anything you don't know is
             filled from sector benchmarks and clearly footnoted as an
             assumption.</p>
          <span class="mode-meta">~3 minutes · Saved as you go</span>
          <span class="mode-go" aria-hidden="true">→</span>
        </button>

        <button class="mode-card" data-nav="builder"
                onClick={() => navigate('/data')}>
          <span class="mode-num">02</span>
          <h2>Bring your data</h2>
          <p>Upload a spreadsheet. We work out which fact table it is, show you
             the field mapping with a confidence score, and tell you exactly what
             it will and will not produce before anything runs.</p>
          <span class="mode-meta">CSV or Excel · Nothing runs until you approve it</span>
          <span class="mode-go" aria-hidden="true">→</span>
        </button>

        <button class="mode-card" data-nav="samples"
                onClick={() => navigate('/samples')}>
          <span class="mode-num">03</span>
          <h2>Browse the samples</h2>
          <p>Four finished companies with real stories — a churn problem, a cash
             problem, a concentration risk, a margin problem. Read what to watch
             for before you run one.</p>
          <span class="mode-meta">Instant · No input needed</span>
          <span class="mode-go" aria-hidden="true">→</span>
        </button>
      </div>

      <div class="surprise-row">
        <div>
          <h3>Feeling lucky?</h3>
          <p>Generate a complete, self-consistent random company and its full
             reporting pack.</p>
        </div>
        <button class="primary" id="btn-surprise" disabled={starting}
                onClick={() => void run('surprise')}>
          Surprise me
        </button>
      </div>
    </section>
  );
}
