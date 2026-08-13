import { useEffect, useState } from 'preact/hooks';
import { HistoryDrawer } from './components/HistoryDrawer';
import { href, navigate, useRoute } from './lib/router';
import { Builder } from './views/Builder';
import { Home } from './views/Home';
import { RunView } from './views/RunView';
import { Samples } from './views/Samples';
import { Studio } from './views/Studio';
import { Survey } from './views/Survey';

function useTheme() {
  const [theme, setTheme] = useState(
    () => document.documentElement.dataset['theme'] ?? 'light');
  useEffect(() => { document.documentElement.dataset['theme'] = theme; }, [theme]);
  return [theme, () => setTheme(theme === 'light' ? 'dark' : 'light')] as const;
}

export function App() {
  const route = useRoute();
  const [theme, toggleTheme] = useTheme();
  const [historyOpen, setHistoryOpen] = useState(false);

  return (
    <>
      <header class="topbar">
        <a class="brand" href={href('/')} onClick={(e) => { e.preventDefault(); navigate('/'); }}>
          <span class="brand-mark" aria-hidden="true" />
          <span class="brand-name">KPI Dashboard Maker</span>
        </a>
        <nav class="topnav">
          <button class="ghost" onClick={() => navigate('/')}>Home</button>
          <button class="ghost" id="btn-history" onClick={() => setHistoryOpen(true)}>
            Recent runs
          </button>
          <button class="ghost" id="btn-theme" aria-label="Toggle theme"
                  onClick={toggleTheme}>
            {theme === 'light' ? 'Dark' : 'Light'}
          </button>
        </nav>
      </header>

      <HistoryDrawer open={historyOpen} onClose={() => setHistoryOpen(false)} />


      <main id="app">
        {route.name === 'home' && <Home />}
        {route.name === 'samples' && <Samples />}
        {route.name === 'survey' && <Survey />}
        {route.name === 'builder' && <Builder />}
        {route.name === 'run' && <RunView runId={route.params['runId'] as string} />}
        {route.name === 'studio' && <Studio runId={route.params['runId'] as string} />}
        {route.name === 'not-found' && (
          <section class="view view-center" id="view-not-found">
            <div class="run-card">
              <h2>No such page</h2>
              <p class="lede"><code>{route.path}</code> is not a screen in this app.</p>
              <button class="primary" onClick={() => navigate('/')}>Back home</button>
            </div>
          </section>
        )}
      </main>
    </>
  );
}
