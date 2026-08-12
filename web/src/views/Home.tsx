import { navigate } from '../lib/router';

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
      </div>
    </section>
  );
}
