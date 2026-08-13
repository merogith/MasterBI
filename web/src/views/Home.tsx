import { DemoNotice } from '../components/DemoNotice';
import { createRun } from '../lib/api';
import { navigate } from '../lib/router';

async function surprise() {
  const run = await createRun({
    mode: 'surprise', seed: Math.floor(Math.random() * 1e7),
  });
  navigate(`/runs/${run.run_id}`);
}

/* The three doors, ported from `ui/index.html`. Copy is unchanged on purpose:
   this is a port, and rewording it here would make any behaviour difference
   impossible to attribute. 2.1 rebuilds this screen deliberately. */
export function Home() {
  return (
    <section class="view" id="view-home">
      <div class="hero">
        <p class="eyebrow">Performance reporting, generated</p>
        <h1>Turn a business into a board pack.</h1>
        <p class="lede">
          Pick the KPIs that actually matter for a company's sector, size and
          objective — then generate the dashboard, the report, the deck and the
          data behind them. Every number is computed, not guessed.
        </p>
      </div>

      <DemoNotice />

      <div class="mode-grid">
        <button class="mode-card" data-nav="samples"
                onClick={() => navigate('/samples')}>
          <span class="mode-num">01</span>
          <h2>Try a sample</h2>
          <p>Four finished companies with real stories — a churn problem, a cash
             problem, a concentration risk, a margin problem. See the complete end
             product in seconds, then adjust any of it in the Studio.</p>
          <span class="mode-meta">Instant · No input needed</span>
          <span class="mode-go" aria-hidden="true">→</span>
        </button>

        <button class="mode-card" data-nav="survey"
                onClick={() => navigate('/survey')}>
          <span class="mode-num">02</span>
          <h2>Build your own</h2>
          <p>Answer 14 questions about your business, plus 5 optional ones that
             sharpen the result. Anything you don't know is filled from sector
             benchmarks and clearly footnoted as an assumption.</p>
          <span class="mode-meta">~3 minutes · Free to run</span>
          <span class="mode-go" aria-hidden="true">→</span>
        </button>

        <button class="mode-card" data-nav="builder"
                onClick={() => navigate('/data')}>
          <span class="mode-num">03</span>
          <h2>Bring your data</h2>
          <p>Upload a spreadsheet, see what's in it, clean it with an ordered
             recipe, and map its columns onto the fact tables with a confidence
             score per field. Anything the data cannot support is dropped with a
             reason rather than invented.</p>
          <span class="mode-meta">CSV or Excel · Nothing runs until you approve it</span>
          <span class="mode-go" aria-hidden="true">→</span>
        </button>
      </div>

      <div class="surprise-row">
        <div>
          <h3>Feeling lucky?</h3>
          <p>Generate a complete, self-consistent random company and its full
             reporting pack.</p>
        </div>
        <button class="primary" id="btn-surprise" onClick={() => void surprise()}>
          Surprise me
        </button>
      </div>
    </section>
  );
}
