import { render } from 'preact';
import { App } from './app';

// The legacy stylesheet, imported rather than copied. One source of truth until
// 1.1c generates the tokens from `kpi_maker/design/`; a second copy here would
// be exactly the drift `tests/test_packaging.py` exists to prevent.
import '../../ui/styles.css';

const root = document.getElementById('root');
if (root === null) throw new Error('no #root to mount into');
render(<App />, root);
