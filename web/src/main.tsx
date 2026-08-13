import { render } from 'preact';
import { App } from './app';

// Moved here with the rest of the front end when `ui/` was deleted. 1.1c
// generates the token block at the top of it from `kpi_maker/design/`, so the
// app, the dashboard, the PDF and the deck cannot drift apart.
import './styles.css';

const root = document.getElementById('root');
if (root === null) throw new Error('no #root to mount into');
render(<App />, root);
