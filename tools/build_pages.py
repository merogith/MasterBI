"""Pre-render the sample gallery into a static site for GitHub Pages.

GitHub Pages serves files; it does not run Python. Mode 1 ("Try a sample") is
the one mode whose answer is fixed in advance, so it can be computed here and
served as flat JSON. Modes 2 and 3 need the generator at click time and are
handled by static_shim.js with an explanation rather than a broken button.

The payloads are produced by calling the *server's own* functions —
`_build_summary`, `list_tables`, `get_table`, `list_samples`, `get_survey` —
against a redirected RUNS_DIR. A second implementation of those shapes would
drift from the API within a release, and the front end would break in ways
only visible on the deployed site.

    python -m tools.build_pages --out site
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kpi_maker.api import server as api          # noqa: E402
from kpi_maker.cli import load_profile, run_pipeline  # noqa: E402

# The banner the static site shows under the hero. The live app never sees it.
STATIC_NOTICE = """
    <div class="notice static-notice" style="margin:0 0 28px">
      <strong>Demo mode.</strong> The three companies below are pre-rendered and
      fully explorable — dashboard, scorecard, every fact table, every download.
      Building your own, uploading a spreadsheet and <em>Surprise me</em> run a
      Python pipeline, which a static host cannot do. Start the app on your own
      machine and <strong>this page connects to it automatically</strong>: every
      mode unlocks and your runs are saved in your own <code>runs/</code> folder.
      Press <em>Run locally</em> in the header for the commands.
    </div>
"""


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, default=str), encoding="utf-8")


def relativise(url: str) -> str:
    """`/files/abc/report.pdf` -> `files/abc/report.pdf`.

    Absolute paths would resolve to merogith.github.io/files/... and 404; the
    site lives one directory down at /MasterBI/.
    """
    return url.lstrip("/") if isinstance(url, str) and url.startswith("/files/") else url


def build(out_dir: Path) -> int:
    site = out_dir.resolve()
    if site.exists():
        shutil.rmtree(site)
    (site / "data" / "runs").mkdir(parents=True)

    # Point the server's run store at the site's file tree, so every helper we
    # borrow below reads and writes exactly where the static site will serve.
    files_root = site / "files"
    files_root.mkdir(parents=True, exist_ok=True)
    api.RUNS_DIR = files_root

    gallery = json.loads((api.SAMPLES_DIR / "gallery.json").read_text(encoding="utf-8"))

    write_json(site / "data" / "samples.json", api.list_samples())
    write_json(site / "data" / "survey.json", api.get_survey())

    index: List[Dict[str, Any]] = []

    for entry in gallery:
        run_id = entry["id"]                     # stable id == stable URLs
        profile = load_profile(api.SAMPLES_DIR / entry["file"])
        run_dir = files_root / run_id
        print(f"  building {run_id} — {profile.identity.name}", flush=True)
        run_pipeline(profile, run_dir, quiet=True)

        summary = api._build_summary(run_id, run_dir, profile)
        summary["steps"] = [
            "Validating profile",
            "Selecting KPIs from the library",
            "Generating data and reconciling",
            "Computing metrics and detecting findings",
            "Rendering dashboard, report, deck and workbook",
        ]
        for artifact in summary.get("artifacts", []):
            artifact["url"] = relativise(artifact["url"])

        # Shape matches GET /api/runs/{id} for an already-finished run.
        write_json(site / "data" / "runs" / run_id / "summary.json",
                   {"run_id": run_id, "status": "done", "summary": summary})

        tables = api.list_tables(run_id)
        for table in tables:
            table["url"] = relativise(table["url"])
        write_json(site / "data" / "runs" / run_id / "tables.json", tables)

        for table in tables:
            write_json(site / "data" / "runs" / run_id / "table" / f"{table['name']}.json",
                       api.get_table(run_id, table["name"]))

        index.append({"run_id": run_id, "status": "done",
                      "company": profile.identity.name, "mode": "sample",
                      "started_at": None})

    write_json(site / "data" / "runs.json", index)

    # Front end, verbatim, plus the shim.
    for name in ("app.js", "styles.css"):
        shutil.copy2(api.UI_DIR / name, site / name)

    html = (api.UI_DIR / "index.html").read_text(encoding="utf-8")
    # The shim loads app.js itself once it knows whether a local server is
    # there, so app.js sees a settled KPI_FILES_BASE rather than one that
    # changes after it has already read it.
    marker = '<script src="app.js"></script>'
    if marker not in html:
        raise SystemExit("index.html no longer loads app.js the expected way")
    html = html.replace(marker, '<script src="static_shim.js"></script>')
    # Anchor the banner to the hero block rather than a line number.
    html = re.sub(r'(<div class="mode-grid">)', STATIC_NOTICE.strip() + r"\n\n    \1",
                  html, count=1)
    (site / "index.html").write_text(html, encoding="utf-8")
    shutil.copy2(Path(__file__).parent / "static_shim.js", site / "static_shim.js")

    # Without this, Jekyll reprocesses the tree and drops anything it does not
    # recognise — including directories that start with an underscore.
    (site / ".nojekyll").write_text("", encoding="utf-8")

    total = sum(f.stat().st_size for f in site.rglob("*") if f.is_file())
    count = sum(1 for f in site.rglob("*") if f.is_file())
    print(f"\nBuilt {count} files, {total / 1e6:.1f} MB -> {site}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="site", help="output directory")
    args = parser.parse_args()
    return build(Path(args.out))


if __name__ == "__main__":
    raise SystemExit(main())
