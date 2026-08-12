"""Checks on the repository's own claims about itself.

This repo's characteristic bug is drift: two places state the same fact and one
of them stops being true. The README said 14 survey questions when there were
19 and 3 sample companies when there were 4; the Pages workflow verified three
of the four, so a broken fourth passed CI; a UI banner said the AI layer was
"not connected in this build" months after it shipped.

Every check here is a fact stated in two places, asserted equal. They are cheap
and they run on every push, which is the only way this class of bug stays dead.
"""
from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _requirement_names(text: str) -> set:
    """Distribution names from a requirements file, ignoring extras and pins."""
    names = set()
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        name = re.split(r"[<>=!\[;]", line, maxsplit=1)[0].strip()
        if name:
            names.add(name.lower().replace("_", "-"))
    return names


def test_requirements_match_pyproject() -> None:
    """`requirements.txt` and `pyproject.toml` must list the same packages.

    Both exist for a reason — the double-click launchers pip-install from
    `requirements.txt` and must not depend on a build backend — so the fix is
    to assert they agree rather than to delete one.
    """
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared = _requirement_names("\n".join(pyproject["project"]["dependencies"]))
    pinned = _requirement_names((ROOT / "requirements.txt").read_text(encoding="utf-8"))
    assert declared == pinned, (
        f"only in pyproject: {sorted(declared - pinned)}\n"
        f"only in requirements.txt: {sorted(pinned - declared)}"
    )


def test_ai_extra_matches_requirements_ai() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared = _requirement_names(
        "\n".join(pyproject["project"]["optional-dependencies"]["ai"]))
    pinned = _requirement_names(
        (ROOT / "requirements-ai.txt").read_text(encoding="utf-8"))
    assert declared == pinned


def test_readme_survey_question_count() -> None:
    """The README and the UI must not misstate how long the survey is."""
    from kpi_maker.survey.questions import CORE_QUESTIONS, QUESTIONS

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    index = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")

    for text, where in ((readme, "README.md"), (index, "ui/index.html")):
        for stated in re.findall(r"(\d+)[- ]question survey", text) + \
                      re.findall(r"Answer (\d+) questions", text):
            assert int(stated) in (len(CORE_QUESTIONS), len(QUESTIONS)), (
                f"{where} claims {stated} questions; there are "
                f"{len(CORE_QUESTIONS)} core and {len(QUESTIONS)} total"
            )


def test_sample_count_is_stated_correctly() -> None:
    """Every place that counts the sample companies must agree with the gallery."""
    gallery = json.loads(
        (ROOT / "samples" / "gallery.json").read_text(encoding="utf-8"))
    n = len(gallery)
    words = {3: "three", 4: "four", 5: "five", 6: "six"}

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    # Both phrasings the README has used: "4 curated companies" in the mode
    # table and "M5, 4 companies" in the status table. The second is how the
    # count drifted last time — one place got updated and the other did not.
    for stated in re.findall(r"(\d+) (?:curated )?(?:sample )?companies", readme):
        assert int(stated) == n, f"README says {stated} samples; gallery has {n}"

    builder = (ROOT / "tools" / "build_pages.py").read_text(encoding="utf-8")
    for stated in re.findall(r"The (\w+) companies below", builder):
        assert stated.lower() == words.get(n, str(n)), (
            f"build_pages.py says '{stated}'; gallery has {n}")


def test_pages_workflow_verifies_every_sample() -> None:
    """CI must check all the samples it builds, not the first three.

    The workflow looped over three hard-coded ids while the gallery held four,
    so a broken `kestrel_retail` would have deployed green.
    """
    gallery = json.loads(
        (ROOT / "samples" / "gallery.json").read_text(encoding="utf-8"))
    workflow = (ROOT / ".github" / "workflows" / "pages.yml").read_text(
        encoding="utf-8")
    for entry in gallery:
        assert entry["id"] in workflow, (
            f"pages.yml never verifies sample {entry['id']!r}")


def test_ci_matrix_matches_the_declared_python_range() -> None:
    """CI must test what `requires-python` claims, and nothing outside it.

    The first CI matrix ran 3.11 and 3.13. `numpy<2` publishes no cp313 wheels
    — support starts at numpy 2.1 — so pip built 1.26.4 from source, which took
    seven minutes on Windows and produced a binary that crashed the interpreter
    on `import`. Testing an unsupported version is not extra safety; it is a
    red job everyone learns to ignore.
    """
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    spec = pyproject["project"]["requires-python"]
    floor = re.search(r">=\s*(\d+)\.(\d+)", spec)
    ceiling = re.search(r"<\s*(\d+)\.(\d+)", spec)
    assert floor and ceiling, f"expected a bounded range, got {spec!r}"
    lo = (int(floor.group(1)), int(floor.group(2)))
    hi = (int(ceiling.group(1)), int(ceiling.group(2)))

    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    matrix = re.search(r"python:\s*\[([^\]]+)\]", workflow)
    assert matrix, "no python matrix found in ci.yml"
    tested = [tuple(int(p) for p in v.strip().strip("'\"").split("."))
              for v in matrix.group(1).split(",")]

    for version in tested:
        assert lo <= version < hi, (
            f"ci.yml tests Python {'.'.join(map(str, version))}, "
            f"which is outside requires-python {spec!r}")
    assert lo in tested, f"the floor {lo} is declared supported but never tested"


def test_no_shipped_capability_is_described_as_missing() -> None:
    """The UI must not tell users a feature is absent when it ships.

    `ui/index.html` carried "the Claude agents that plan and narrate a run are
    *not connected in this build*" for months after `ai/planner.py`,
    `ai/narrator.py` and `ai/verify.py` landed and were covered by tests — so
    the product was actively under-selling its best work while stranding the
    user on a screen with no next step.
    """
    from kpi_maker.ai import narrator, planner, verify  # noqa: F401

    for name in ("ui/index.html", "ui/app.js", "kpi_maker/api/server.py"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "not connected in this build" not in text, (
            f"{name} still claims a shipped capability is missing")


def test_the_legacy_upload_route_is_gone() -> None:
    """One upload route, and it is the one that reads files properly.

    `POST /api/upload` parsed with a bare `pd.read_csv` — no encoding chain, no
    delimiter sniff, no title-block handling, none of what `ingest/readers.py`
    does — and returned a hard-coded note claiming the mapping layer did not
    exist. `POST /api/ingest/profile` supersedes it entirely.
    """
    from kpi_maker.api.server import app

    paths = {route.path for route in app.routes}
    assert "/api/upload" not in paths, "the superseded upload route is still registered"
    assert "/api/ingest/profile" in paths

    for name in ("ui/app.js", "tools/static_shim.js"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "/api/upload" not in text, f"{name} still calls the deleted route"


def test_every_chart_tab_has_a_label() -> None:
    """A chart declaring a tab the dashboard cannot name gets `t.title()`.

    That is how the four e-commerce exhibits ended up under a heading reading
    "Retention" — not chosen, just the fallback capitalising the key.
    """
    from kpi_maker.render.dashboard import TAB_LABELS

    # The tab is set on the `ChartSpec` each builder returns, not on its
    # registry entry, so there is nothing to introspect without data to build
    # against. Read the literals out of the source instead: it is a static fact
    # about the module and this keeps the check free.
    source = (ROOT / "kpi_maker" / "viz" / "charts.py").read_text(encoding="utf-8")
    declared = set(re.findall(r'\btab\s*=\s*"([a-z_]+)"', source))
    assert declared, "found no tab= literals — has charts.py changed shape?"

    missing = declared - set(TAB_LABELS)
    assert not missing, f"chart tabs with no label in TAB_LABELS: {sorted(missing)}"


def test_static_shim_only_uses_defined_css_variables() -> None:
    """The hosted pill must not reference tokens the stylesheet never defines.

    `--line`, `--card`, `--ink`, `--bg` and `--warn` were all undefined, so the
    injected "Run locally" pill fell through to its hard-coded light fallbacks
    and rendered white-on-white in dark mode. The shim is injected into the
    same page as `styles.css`, so its variables have to come from there.
    """
    shim = (ROOT / "tools" / "static_shim.js").read_text(encoding="utf-8")
    styles = (ROOT / "ui" / "styles.css").read_text(encoding="utf-8")

    defined = set(re.findall(r"^\s+(--[a-z-]+):", styles, re.MULTILINE))
    used = set(re.findall(r"var\((--[a-z-]+)", shim))
    assert used <= defined, (
        f"static_shim.js uses undefined CSS variables: {sorted(used - defined)}")


def test_unimplemented_kpis_never_reach_a_scorecard() -> None:
    """A record sheet with no implementation must drop, not render broken.

    `nps` and `support_first_response_hours` are declared `kind: builtin` with
    no registered `@metric`. Carrying the definition ahead of the
    implementation is legitimate — the record sheet is documentation as much as
    code — but selecting one is not: `atlas_enterprise` lists `support_desk` in
    `data_availability.has`, so `requires_data` let it through and it appeared
    on the scorecard reporting "no implementation registered for this KPI id".

    The guarantee is about the outcome, so that is what this asserts: whatever
    the library holds, nothing unimplemented survives selection, and anything
    dropped for that reason says so.
    """
    import yaml

    from kpi_maker.cli import load_profile
    from kpi_maker.kpi.selection import select
    from kpi_maker.metrics.engine import _REGISTRY as REGISTRY

    unimplemented = set()
    for path in sorted((ROOT / "kpi_maker" / "kpi" / "library").glob("*.yaml")):
        for entry in yaml.safe_load(path.read_text(encoding="utf-8")) or []:
            compute = entry.get("compute") or {}
            if compute.get("kind", "builtin") != "builtin":
                continue
            if (compute.get("ref") or entry["id"]) not in REGISTRY:
                unimplemented.add(entry["id"])

    for sample in sorted((ROOT / "samples").glob("*.json")):
        if sample.name == "gallery.json":
            continue
        kpi_set = select(load_profile(sample))
        leaked = unimplemented & {k.id for k in kpi_set.kpis}
        assert not leaked, (
            f"{sample.name}: unimplemented KPIs on the scorecard: {sorted(leaked)}")
        for kpi_id in unimplemented & set(kpi_set.dropped):
            assert kpi_set.dropped[kpi_id], (
                f"{sample.name}: {kpi_id} dropped with no reason")


def test_the_ui_holds_no_copy_of_the_engines_stage_names() -> None:
    """The running screen must render what the server sends, and nothing else.

    `ui/app.js` used to carry `EXPECTED_STEPS`, five stage labels it ticked off
    by string equality against what the server echoed — while the engine has
    seventeen stages and the server reported none of them until the run was
    already over. `tools/build_pages.py` carried the same five strings again.
    Two hand-maintained copies of a vocabulary that lives in `graph.Stage.label`
    is the drift this whole file exists to prevent, so assert the copies stay
    deleted rather than trusting that they will.
    """
    from kpi_maker.pipeline.graph import STAGES

    labels = {s.label for s in STAGES.values()}
    # The rewritten front end is included: a port is exactly when a deleted
    # hardcoded list gets helpfully retyped into the new code.
    sources = [ROOT / rel for rel in
               ("ui/app.js", "tools/build_pages.py", "tools/static_shim.js")]
    sources += sorted((ROOT / "web" / "src").rglob("*.ts"))
    sources += sorted((ROOT / "web" / "src").rglob("*.tsx"))

    for path in sources:
        text = path.read_text(encoding="utf-8")
        found = sorted(label for label in labels if f'"{label}"' in text
                       or f"'{label}'" in text)
        assert not found, (
            f"{path.relative_to(ROOT)} hardcodes engine stage labels: {found}")


def test_the_running_screen_can_actually_cancel() -> None:
    """Cancel used to clear a timer and navigate home while the job ran on.

    Three things have to line up for it to mean anything, and each is in a
    different file: the button posts to the endpoint, the endpoint exists, and
    the poll handles the status it produces. Asserting only one of the three
    is how a working-looking Cancel came to do nothing for so long.
    """
    app_js = (ROOT / "ui" / "app.js").read_text(encoding="utf-8")
    server = (ROOT / "kpi_maker" / "api" / "server.py").read_text(encoding="utf-8")

    assert "/cancel`, { method: 'POST' }" in app_js, \
        "the Cancel button does not call the cancel endpoint"
    assert '@app.post("/api/runs/{run_id}/cancel")' in server, \
        "there is no cancel endpoint to call"
    assert "'cancelled'" in app_js, \
        "the poll does not handle a cancelled run"
    assert 'status="cancelled"' in server, \
        "the server never reports a run as cancelled"


def test_no_run_database_is_committed() -> None:
    """`runs.db` is local state, like the run directories it indexes.

    It lands inside `runs/`, which is already ignored, but the path moves with
    `RUNS_DIR` and a stray copy would commit one machine's history — and, in the
    Pages build's case, publish it.
    """
    found = sorted(str(p.relative_to(ROOT)) for p in ROOT.rglob("runs.db")
                   if ".venv" not in p.parts and ".git" not in p.parts)
    tracked = [p for p in found if not p.startswith(("runs/", "site/", "out/"))]
    assert not tracked, f"a run database escaped its ignored directory: {tracked}"


def test_history_reads_the_index_not_a_directory_scan() -> None:
    """`list_runs` must not go back to globbing `summary.json` for its truth.

    That scan could only see runs that finished, which is why every recovered
    run reported mode "restored" with no start time, and why a cancelled run —
    which deliberately writes no `summary.json` — vanished from history along
    with the stages 0.6 kept on disk for it. The index replaced it; this keeps
    it replaced.
    """
    server = (ROOT / "kpi_maker" / "api" / "server.py").read_text(encoding="utf-8")
    body = server.split('@app.get("/api/runs")\n', 1)[1].split("\n@app.", 1)[0]

    assert 'glob("*/summary.json")' not in body, \
        "list_runs is scanning directories again instead of reading the index"
    assert "_store()" in body, "list_runs does not read the run index"
    assert '"mode": "restored"' not in body, \
        "list_runs is labelling recovered runs again instead of storing the mode"


def test_the_history_drawer_can_resume_what_it_lists() -> None:
    """0.7 made cancelled runs visible again; a row you cannot act on is half a fix.

    Four things have to line up, in three files: the row offers Resume, the
    server says whether a resume is possible, the drawer posts to the re-run
    endpoint, and that endpoint exists. Asserting one of the four is how a
    working-looking button comes to do nothing — which is what `test_the_
    running_screen_can_actually_cancel` above exists to remember.
    """
    app_js = (ROOT / "ui" / "app.js").read_text(encoding="utf-8")
    server = (ROOT / "kpi_maker" / "api" / "server.py").read_text(encoding="utf-8")

    assert "data-resume-run" in app_js, "the drawer offers no way to resume a run"
    assert "r.resumable" in app_js, \
        "the drawer offers Resume without checking the run can be resumed"
    assert '"resumable"' in server, "the server never says whether a run is resumable"
    assert '@app.post("/api/runs/{run_id}/rerun")' in server, \
        "there is no re-run endpoint for Resume to call"


def test_the_rewrite_declares_itself_partial() -> None:
    """While the port is incomplete, the app must say so on screen.

    `MASTERBI_UI=next` is one environment variable away from being what a user
    sees, and the rewrite is missing the survey, the upload funnel, the Studio
    and the history drawer. The banner comes out when they land — this is what
    stops it coming out early.
    """
    app = (ROOT / "web" / "src" / "app.tsx").read_text(encoding="utf-8")
    router = (ROOT / "web" / "src" / "lib" / "router.ts").read_text(encoding="utf-8")

    ported = {"home", "samples", "survey", "builder", "run"}
    declared = set(re.findall(r"\['/[^']*',\s*'([a-z-]+)'\]", router))
    assert declared == ported, (
        f"routes changed to {sorted(declared)}; update the preview banner in "
        "app.tsx and this list together, or drop both if the port is complete")
    assert "Rewrite preview" in app, \
        "the partial rewrite no longer tells the user it is partial"


def test_the_launchers_gate_on_the_python_floor_they_need() -> None:
    """The double-click launchers must refuse the versions pip will refuse.

    `start.command` gated on 3.9 while `requires-python` had moved to 3.11, so
    a user on 3.9 or 3.10 got a built virtualenv and then a pip resolution
    failure with nothing in it that names the actual problem. The README stated
    the same wrong floor. All three read from one fact.
    """
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    floor = re.search(r">=\s*(\d+)\.(\d+)", pyproject["project"]["requires-python"])
    assert floor
    major, minor = floor.group(1), floor.group(2)

    launcher = (ROOT / "start.command").read_text(encoding="utf-8")
    assert f"({major},{minor})" in launcher, \
        f"start.command does not gate on Python {major}.{minor}"
    assert f"Python {major}.{minor} or newer" in launcher, \
        "start.command's message states a different floor than it enforces"

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for stated in re.findall(r"Needs Python (\d+\.\d+) or newer", readme):
        assert stated == f"{major}.{minor}", \
            f"README says Python {stated}; requires-python says {major}.{minor}"


def test_the_readme_counts_the_sector_packs_correctly() -> None:
    """The gap list must agree with the status table two screens above it.

    "Only the SaaS pack exists" survived in Known gaps while the same file's
    status table already said "2 of 10 have their own archetype and pack" — the
    e-commerce pack and 0.1's cross-sector fallback had both landed. A file
    that contradicts itself is worse than one that is merely out of date.
    """
    from kpi_maker.profile.sectors import supported_sectors

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    words = {1: "One", 2: "Two", 3: "Three", 4: "Four", 5: "Five"}
    n = len(supported_sectors())

    assert f"**{words[n]} sectors have their own KPI pack**" in readme, (
        f"{n} sectors have their own pack; the Known gaps section says "
        "something else")
    for stated in re.findall(r"(\d+) of 10 have their own archetype and pack",
                             readme):
        assert int(stated) == n, (
            f"the status table says {stated} of 10; there are {n}")
