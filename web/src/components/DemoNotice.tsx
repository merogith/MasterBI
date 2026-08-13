import { useIsStatic } from '../lib/useStatic';

/* Demo mode, said by the app rather than stamped into the HTML.
 *
 * Whether this is a frozen demo or a live local server is a fact about the
 * product, not about the page chrome — and the Pages build used to inject this
 * paragraph by regex into `ui/index.html`, which meant it could only ever
 * appear on a screen that existed as static markup. `tools/static_shim.js`
 * owns the flag and announces when its probe settles.
 */
export function DemoNotice() {
  if (!useIsStatic()) return null;

  return (
    <div class="notice static-notice" style={{ margin: '0 0 28px' }}>
      <strong>Demo mode.</strong> The four companies below are pre-rendered and
      fully explorable — dashboard, scorecard, every fact table, every download.
      Building your own, uploading a spreadsheet and <em>Surprise me</em> run a
      Python pipeline, which a static host cannot do. Start the app on your own
      machine and <strong>this page connects to it automatically</strong>: every
      mode unlocks and your runs are saved in your own <code>runs/</code> folder.
      Press <em>Run locally</em> in the header for the commands.
    </div>
  );
}
