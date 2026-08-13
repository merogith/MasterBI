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

from kpi_maker.api import server as api  # noqa: E402
from kpi_maker.cli import load_profile, run_pipeline  # noqa: E402


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

    # What the Studio needs to render. Without these the most differentiated
    # screen in the product 404s for everyone who clicks the public link — it
    # was reachable and broken, so the button had to be disabled. Frozen here,
    # it opens read-only instead.
    write_json(site / "data" / "catalog" / "options.json", api.catalog_options())
    write_json(site / "data" / "catalog" / "kpis.json", api.list_kpis())
    # Not the build machine's AI status: CI may hold a key, and a demo that
    # advertised a working planner it cannot run would be the same lie in a new
    # place. The static site can never call a model, so it says so.
    write_json(site / "data" / "ai" / "status.json", {
        "available": False,
        "reason": "The hosted demo cannot call a model. Run the app locally "
                  "with an API key to use the planner and the narrator.",
        "narratable_sections": [],
    })

    index: List[Dict[str, Any]] = []

    for entry in gallery:
        run_id = entry["id"]                     # stable id == stable URLs
        profile = load_profile(api.SAMPLES_DIR / entry["file"])
        run_dir = files_root / run_id
        print(f"  building {run_id} — {profile.identity.name}", flush=True)
        run_pipeline(profile, run_dir, quiet=True)

        summary = api._build_summary(run_id, run_dir, profile)
        for artifact in summary.get("artifacts", []):
            artifact["url"] = relativise(artifact["url"])

        # Shape matches GET /api/runs/{id} for an already-finished run.
        write_json(site / "data" / "runs" / run_id / "summary.json",
                   {"run_id": run_id, "status": "done", "summary": summary})

        tables = api.list_tables(run_id)
        for table in tables:
            table["url"] = relativise(table["url"])
        write_json(site / "data" / "runs" / run_id / "tables.json", tables)

        # The spec that produced this run, so the Studio can show what it was
        # built from even where nothing can be changed.
        write_json(site / "data" / "runs" / run_id / "spec.json",
                   api.get_spec(run_id))

        for table in tables:
            write_json(site / "data" / "runs" / run_id / "table" / f"{table['name']}.json",
                       api.get_table(run_id, table["name"]))

        index.append({"run_id": run_id, "status": "done",
                      "company": profile.identity.name, "mode": "sample",
                      "started_at": None})

    write_json(site / "data" / "runs.json", index)

    # Front end: the Vite bundle built for this sub-path, plus the shim.
    dist = ROOT / "web" / "dist-pages"
    if not (dist / "index.html").exists():
        raise SystemExit(
            "no Pages bundle — run `npm --prefix web ci && "
            "npm --prefix web run build:pages` first")
    shutil.copytree(dist / "assets", site / "assets")

    html = (dist / "index.html").read_text(encoding="utf-8")

    # The deployment root, taken from what Vite actually emitted rather than
    # restated here — one fact, one place. Everything the page loads has to be
    # absolute against it, because 404.html serves deep links from directories
    # that do not exist.
    asset = re.search(r'src="([^"]*/assets/[^"]+\.js)"', html)
    if asset is None:
        raise SystemExit("the Vite bundle's index.html has no module script")
    base = asset.group(1).split("assets/")[0]

    # The shim has to replace `window.fetch` before the app issues a request.
    # A classic script runs the moment it is parsed and a module script is
    # deferred to after parsing, so this ordering is guaranteed by the spec —
    # no coordination between the two files required.
    marker = "</head>"
    if marker not in html:
        raise SystemExit("the Vite bundle's index.html has no <head> to patch")
    html = html.replace(marker, (
        f'  <script>window.KPI_BASE = "{base}";</script>\n'
        f'  <script src="{base}static_shim.js"></script>\n' + marker), 1)
    (site / "index.html").write_text(html, encoding="utf-8")

    # GitHub Pages has no rewrite rule, so a deep link — `/MasterBI/samples`, or
    # any run URL someone shares — is a 404 from a static host. Serving the same
    # shell as the 404 page is the standard answer: the app boots and reads
    # `location.pathname` itself, so the link resolves to the screen it names.
    (site / "404.html").write_text(html, encoding="utf-8")

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
