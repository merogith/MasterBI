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

import pytest

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
    home = (ROOT / "web" / "src" / "views" / "Home.tsx").read_text(encoding="utf-8")

    for text, where in ((readme, "README.md"), (home, "web/src/views/Home.tsx")):
        for stated in re.findall(r"(\d+)[- ]question survey", text) + \
                      re.findall(r"Answer (\d+) questions", text):
            assert int(stated) in (len(CORE_QUESTIONS), len(QUESTIONS)), (
                f"{where} claims {stated} questions; there are "
                f"{len(CORE_QUESTIONS)} core and {len(QUESTIONS)} total"
            )
        # The phrasing the README actually used, which the two patterns above
        # did not match — so "14 core questions, 5 optional" sat unchecked
        # through every change to the bank. A drift test with a hole in it is
        # the same false assurance as a docstring naming a test nobody wrote.
        for core, optional in re.findall(r"(\d+) core questions?, (\d+) optional", text):
            assert int(core) == len(CORE_QUESTIONS), (
                f"{where} claims {core} core questions; there are "
                f"{len(CORE_QUESTIONS)}")
            assert int(optional) == len(QUESTIONS) - len(CORE_QUESTIONS), (
                f"{where} claims {optional} optional questions; there are "
                f"{len(QUESTIONS) - len(CORE_QUESTIONS)}")


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

    The old `ui/index.html` carried "the Claude agents that plan and narrate a run are
    *not connected in this build*" for months after `ai/planner.py`,
    `ai/narrator.py` and `ai/verify.py` landed and were covered by tests — so
    the product was actively under-selling its best work while stranding the
    user on a screen with no next step.
    """
    from kpi_maker.ai import narrator, planner, verify  # noqa: F401

    sources = [ROOT / "kpi_maker" / "api" / "server.py"]
    sources += sorted((ROOT / "web" / "src").rglob("*.tsx"))
    for path in sources:
        name, text = path.relative_to(ROOT), path.read_text(encoding="utf-8")
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

    sources = [ROOT / "tools" / "static_shim.js"]
    sources += sorted((ROOT / "web" / "src").rglob("*.ts"))
    sources += sorted((ROOT / "web" / "src").rglob("*.tsx"))
    for path in sources:
        text = path.read_text(encoding="utf-8")
        assert "/api/upload" not in text, (
            f"{path.relative_to(ROOT)} still calls the deleted route")


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
    # Both halves of the token set: the generated engine palette and the
    # chrome-only additions beside it.
    styles = "\n".join(
        (ROOT / "web" / "src" / name).read_text(encoding="utf-8")
        for name in ("tokens.css", "styles.css"))

    defined = set(re.findall(r"^\s+(--[a-z0-9-]+):", styles, re.MULTILINE))
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
               ("tools/build_pages.py", "tools/static_shim.js")]
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
    server = (ROOT / "kpi_maker" / "api" / "server.py").read_text(encoding="utf-8")
    client = (ROOT / "web" / "src" / "lib" / "api.ts").read_text(encoding="utf-8")
    running = (ROOT / "web" / "src" / "views" / "Running.tsx").read_text(encoding="utf-8")
    run_view = (ROOT / "web" / "src" / "views" / "RunView.tsx").read_text(encoding="utf-8")

    assert "/cancel`, { method: 'POST' }" in client, \
        "the client does not call the cancel endpoint"
    assert '@app.post("/api/runs/{run_id}/cancel")' in server, \
        "there is no cancel endpoint to call"
    assert "cancelRun" in running, "the Cancel button does not call it"
    assert "'cancelled'" in run_view, \
        "the screen does not handle a cancelled run"
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
    server = (ROOT / "kpi_maker" / "api" / "server.py").read_text(encoding="utf-8")
    drawer = (ROOT / "web" / "src" / "components" / "HistoryDrawer.tsx").read_text(
        encoding="utf-8")

    assert "data-resume-run" in drawer, "the drawer offers no way to resume a run"
    assert "run.resumable" in drawer, \
        "the drawer offers Resume without checking the run can be resumed"
    assert '"resumable"' in server, "the server never says whether a run is resumable"
    assert '@app.post("/api/runs/{run_id}/rerun")' in server, \
        "there is no re-run endpoint for Resume to call"


def test_every_screen_has_a_route() -> None:
    """The route table is the list of screens that exist.

    Only routes that exist may be listed: an entry pointing at an unported
    screen is a link that 404s inside its own app. This is what the preview
    banner's check turned into once the port was complete.
    """
    router = (ROOT / "web" / "src" / "lib" / "router.ts").read_text(encoding="utf-8")
    declared = set(re.findall(r"\['/[^']*',\s*'([a-z-]+)'\]", router))
    assert declared == {"home", "samples", "survey", "builder", "run", "studio"}, (
        f"routes are now {sorted(declared)} — if a screen was added or removed, "
        "update this list and the README's front-end section together")


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


#: Spelled-out numbers, wide enough for every sector count the taxonomy can
#: reach. Two of these checks used to hold their own short dict and both went
#: quiet the moment a count moved past the last key they happened to list.
NUMBER_WORDS = dict(enumerate(
    ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
     "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
     "sixteen", "seventeen", "eighteen", "nineteen", "twenty")))


def test_the_readme_counts_the_sector_packs_correctly() -> None:
    """The gap list must agree with the status table two screens above it.

    "Only the SaaS pack exists" survived in Known gaps while the same file's
    status table already said "2 of 10 have their own archetype and pack" — the
    e-commerce pack and 0.1's cross-sector fallback had both landed. A file
    that contradicts itself is worse than one that is merely out of date.
    """
    from kpi_maker.profile.sectors import declared_sectors, supported_sectors

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    words = {n: w.capitalize() for n, w in NUMBER_WORDS.items()}
    n, total = len(supported_sectors()), len(declared_sectors())

    assert f"**{words[n]} sectors have their own KPI pack**" in readme, (
        f"{n} sectors have their own pack; the Known gaps section says "
        "something else")

    # **Two counts, not one, and they stopped being the same number.** The row
    # read "2 of 20 have their own archetype and pack" and this check only
    # verified the pack half, so it stayed green through 4.2 while seven sectors
    # gained their own generator. One sentence covering two facts is a sentence
    # that can only be half wrong, which is the hardest kind to notice.
    from kpi_maker.profile import taxonomy

    with_archetype = sum(1 for x in taxonomy.load().sectors if x.exact_archetype)

    stated = re.findall(
        r"(\d+) of (\d+) have their own generator archetype and "
        r"(\d+) of (\d+) have their own KPI pack", readme)
    assert stated, "the status table no longer states the sector counts at all"
    for archetypes, arch_total, packs, pack_total in stated:
        assert (int(archetypes), int(arch_total)) == (with_archetype, total), (
            f"the status table says {archetypes} of {arch_total} have their own "
            f"archetype; it is {with_archetype} of {total}")
        assert (int(packs), int(pack_total)) == (n, total), (
            f"the status table says {packs} of {pack_total} have their own "
            f"pack; it is {n} of {total}")

    # The denominator is checked above for the same reason 4.1c added it: the
    # pattern was once pinned to the literal `of 10`, so it matched nothing and
    # went quietly vacuous while the README said "2 of 10".

    # A dict that has to be extended every time a pack lands is how this
    # check kept going stale; the words go up to the sector count instead.
    remaining = NUMBER_WORDS.get(total - n)
    assert remaining, (
        f"{total - n} sectors run on the fallback and this check has no word "
        f"for that number, so it is about to pass by saying nothing")
    assert f"The other\n  {remaining} run on" in readme or \
        f"The other {remaining} run on" in readme, (
            f"{total - n} sectors run on the fallback; Known gaps says "
            "something else")


def test_there_is_one_front_end() -> None:
    """`ui/` is gone, and nothing may quietly reintroduce a second copy.

    The Pages demo used to be built by copying `ui/app.js` and patching
    `ui/index.html` by regex, which is why two front ends existed at all. Both
    targets now come from `web/`, so the hosted demo and the local app cannot
    drift — the property `static_shim.js` claimed in its header comment for a
    year while a second copy sat next to it.
    """
    assert not (ROOT / "ui").exists(), "the legacy front end is back"

    server = (ROOT / "kpi_maker" / "api" / "server.py").read_text(encoding="utf-8")
    assert "UI_DIR" not in server, "the server still references the deleted ui/"

    builder = (ROOT / "tools" / "build_pages.py").read_text(encoding="utf-8")
    assert "web/dist-pages" in builder or "dist-pages" in builder, \
        "the Pages build no longer ships the Vite bundle"

    package = json.loads((ROOT / "web" / "package.json").read_text(encoding="utf-8"))
    assert "build:pages" in package["scripts"], \
        "there is no Pages target to build the demo from"


def test_the_pages_build_serves_deep_links() -> None:
    """A shared run URL has to resolve on a host with no rewrite rule.

    GitHub Pages answers an unknown path with 404.html, so that file has to be
    the app shell. Without it every link into the demo except the root — which
    is every link anyone would actually send — lands on a 404 page.
    """
    builder = (ROOT / "tools" / "build_pages.py").read_text(encoding="utf-8")
    assert '"404.html"' in builder, \
        "the Pages build writes no 404.html, so deep links 404"


def test_artifact_urls_are_joined_not_concatenated() -> None:
    """The download links have to work on both hosts, and they differ.

    The server hands out `/files/<run>/report.pdf` and serves it from the root;
    the frozen demo hands out `files/<run>/report.pdf` and supplies the site
    root separately, because it lives under a repository sub-path. Adding the
    two together produced `/MasterBIfiles/...` — a 404 on every download card
    in the hosted demo, and invisible from the server, where the base is empty.
    """
    client = (ROOT / "web" / "src" / "lib" / "api.ts").read_text(encoding="utf-8")
    results = (ROOT / "web" / "src" / "views" / "Results.tsx").read_text(encoding="utf-8")

    assert "export function fileUrl" in client, "the join helper is gone"
    assert "filesBase() +" not in results, \
        "Results.tsx is concatenating a base and a path again"
    assert results.count("fileUrl(") >= 2, \
        "the dashboard link and the download cards must both use it"


def test_the_shim_resolves_against_the_deployment_root() -> None:
    """Deep links are served by 404.html from directories that do not exist.

    `new URL('.', location.href)` on `/MasterBI/runs/abc` yields
    `/MasterBI/runs/`, so the shim would look for its own assets and the frozen
    JSON one directory too deep — on exactly the URLs people share. The build
    stamps the real root in as `window.KPI_BASE`.
    """
    shim = (ROOT / "tools" / "static_shim.js").read_text(encoding="utf-8")
    builder = (ROOT / "tools" / "build_pages.py").read_text(encoding="utf-8")

    assert "window.KPI_BASE" in shim, "the shim ignores the deployment root"
    # Resolved against the page: `new URL(rel, base)` rejects a base that is
    # not absolute, and the stamped value is a root-relative path.
    assert "new URL(window.KPI_BASE || '.', window.location.href)" in shim, \
        "a root-relative base must be resolved before it is used as a URL base"
    assert "window.KPI_BASE =" in builder, "the build stamps no deployment root"


def test_the_generated_tokens_are_current() -> None:
    """`web/src/tokens.css` is generated; a stale copy is a silent disagreement.

    The stylesheet used to restate the engine's palette by hand under a comment
    claiming the two matched. Nothing checked it, so the app chrome and the
    charts drawn inside it could drift apart with nobody noticing until a
    screenshot looked wrong. Change a token in `viz/theme.py` and forget to run
    the generator, and this is red.
    """
    from tools.gen_tokens import TARGET, render

    assert TARGET.exists(), "tokens.css has not been generated"
    assert TARGET.read_text(encoding="utf-8") == render(), (
        "web/src/tokens.css is stale — run `python -m tools.gen_tokens`")


def test_the_stylesheet_does_not_redefine_engine_tokens() -> None:
    """Chrome-only tokens live beside the generated ones, never on top of them.

    A `--critical` redefined in `styles.css` would win by import order and put
    the app back to disagreeing with the PDF — the exact failure the generator
    exists to prevent, reintroduced one line at a time.
    """
    from kpi_maker.viz.theme import TOKENS

    owned = {f"--{name.replace('_', '-')}" for name in TOKENS["light"]}
    owned |= {"--accent", "--accent-soft", "--font"}

    styles = (ROOT / "web" / "src" / "styles.css").read_text(encoding="utf-8")
    declared = set(re.findall(r"^\s+(--[a-z0-9-]+):", styles, re.MULTILINE))

    clash = sorted(declared & owned)
    assert not clash, (
        f"styles.css redefines engine-owned tokens: {clash}. Change them in "
        "kpi_maker/viz/theme.py and regenerate.")


def test_the_shipped_palette_meets_its_own_thresholds() -> None:
    """The palette holds itself to the bar it holds users to.

    Checked with the engine's own validator rather than a second copy of the
    maths — a second one would eventually disagree with the one gating user
    input, and then a brand colour would be refused for failing a bar the
    shipped palette quietly did not clear.

    Which is not hypothetical: `series_3` was `#1baf7a` at 2.74:1, below the
    3:1 floor `derive_tokens` imposes on a user's brand colour. The floor was
    applied to slot 1 only, so the shipped companions were never held to it.
    It has since been darkened by `ensure_readable` — the same correction a
    user would have got — so every slot can now be asserted, which is what
    makes this test worth having rather than a restatement of the status quo.
    """
    from kpi_maker.design.contrast import (
        AA_TEXT,
        GRAPHICAL,
        MIN_DELTA_E,
        distinguishable,
        ratio,
    )
    from kpi_maker.viz.theme import MAX_CATEGORICAL_SERIES, TOKENS

    for mode, tokens in TOKENS.items():
        page, surface = tokens["page"], tokens["surface"]

        assert ratio(tokens["text_primary"], page) >= AA_TEXT, (
            f"{mode}: body text fails AA against the page")
        assert ratio(tokens["text_secondary"], page) >= AA_TEXT, (
            f"{mode}: secondary text fails AA against the page")

        series = [tokens[f"series_{i}"] for i in range(1, MAX_CATEGORICAL_SERIES + 1)]
        for index, colour in enumerate(series, start=1):
            assert ratio(colour, surface) >= GRAPHICAL, (
                f"{mode}: series_{index} is below the floor a user's own brand "
                f"colour would be corrected to meet ({ratio(colour, surface):.2f}:1)")

        # Including under colour vision deficiency, which is what
        # `distinguishable` simulates — three categorical series separated only
        # by hue are three series a deuteranope reads as one.
        report = distinguishable(series, MIN_DELTA_E)
        assert report["ok"], f"{mode}: series are not distinguishable — {report}"


def test_the_demo_freezes_what_the_studio_needs() -> None:
    """The Studio is reachable on the hosted demo, so it has to work there.

    `/runs/:id/studio` is a real route: a visitor gets to it from the results
    screen, and before these stand-ins existed it opened onto an error. Four
    endpoints back that screen and every one has to be frozen at build time —
    the shim answers from files, and a missing file is a 404 in front of a
    visitor rather than a failure here.
    """
    builder = (ROOT / "tools" / "build_pages.py").read_text(encoding="utf-8")
    shim = (ROOT / "tools" / "static_shim.js").read_text(encoding="utf-8")

    for frozen in ("catalog/options.json", "catalog/kpis.json", "ai/status.json"):
        stem = frozen.rsplit(".", 1)[0]
        assert stem.replace("/", '" / "') in builder or frozen in builder, (
            f"build_pages.py does not freeze {frozen}")
        assert f"data/{frozen}" in shim, f"the shim has no stand-in for {frozen}"

    assert '"spec.json"' in builder, "build_pages.py freezes no run spec"
    assert "data/runs/${spec[1]}/spec.json" in shim, \
        "the shim cannot answer a spec request"


def test_the_demos_studio_cannot_be_edited() -> None:
    """Read-only, and enforced structurally rather than per control.

    A static host cannot re-run a pipeline, so an editable Studio there would
    collect changes it can never apply. One `disabled` fieldset covers every
    control including ones added later; a prop threaded through eight panels
    would be forgotten by the ninth.
    """
    studio = (ROOT / "web" / "src" / "views" / "Studio.tsx").read_text(encoding="utf-8")

    assert "useIsStatic" in studio, "the Studio does not know it is on the demo"
    assert "disabled={readOnly}" in studio, \
        "the panels are not disabled where nothing can be re-run"
    assert "disabled={readOnly || dirty.length === 0}" in studio, \
        "Re-run is offered where there is nothing to re-run with"


def test_the_deploy_builds_the_front_end_it_serves() -> None:
    """`render.yaml` shipped a server with nothing behind it.

    `kpi_maker/ui_dist/` is a build artifact and not committed, so a
    `pip install` build produced a deploy whose `/` answered 500. The image
    builds the front end in a stage of its own, and CI builds the image.
    """
    render = (ROOT / "render.yaml").read_text(encoding="utf-8")
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "runtime: docker" in render, \
        "the blueprint is back to a runtime that cannot build the front end"
    assert "npm --prefix web run build" in dockerfile, \
        "the image does not build the front end"
    assert "ui_dist" in dockerfile, "the image never copies the built bundle in"
    assert "docker build" in ci, "nothing builds the deployable image"


def test_no_overlay_registers_its_escape_key_in_an_effect() -> None:
    """Something a user can see must already work.

    All three overlays — the record sheet, the tour, the history drawer —
    registered a `keydown` listener inside `useEffect`. Preact flushes effects
    after paint, racing `requestAnimationFrame` against `setTimeout(flush, 35)`,
    so each panel was painted, focusable and clickable while the listener that
    dismisses it did not exist yet. Locally that window closed before anyone
    could act; on a CI runner it did not, and two of the three failed there
    while passing here.

    A `keydown` handler on the element is part of the same commit that puts the
    element on screen, so there is no window. Asserted at the source because
    the tour's *appearance* is legitimately effect-driven (its steps are
    filtered once its targets exist), which makes the behavioural version of
    this check impossible for one of the three — and a rule two thirds enforced
    is how the third one comes back.
    """
    overlays = {
        "components/Scorecard.tsx": "the KPI record sheet",
        "components/Tour.tsx": "the tour",
        "components/HistoryDrawer.tsx": "the history drawer",
    }
    offenders = []
    for path, what in overlays.items():
        source = (ROOT / "web" / "src" / path).read_text(encoding="utf-8")
        if "'keydown'" in source or '"keydown"' in source:
            offenders.append(f"{what} ({path})")
        if "onKeyDown" not in source:
            offenders.append(f"{what} has no key handler on the element ({path})")

    assert not offenders, \
        "an overlay is back to a window listener registered after paint: " \
        + ", ".join(offenders)


def test_the_studio_asks_for_its_own_run_s_tables() -> None:
    """The server scopes the fact-table list to a run's archetype, and only if
    it is told which run.

    The endpoint and the caller are in different languages, so nothing else
    connects them: `getOptions()` with no argument gets the union back and the
    Studio silently returns to offering a retailer `mrr_movements`. That is the
    same shape as the Cancel drift check — a fix that works only while both
    halves agree needs something asserting they still do.
    """
    studio = (ROOT / "web" / "src" / "views" / "Studio.tsx").read_text(encoding="utf-8")
    assert "getOptions(runId)" in studio, \
        "the Studio asks for the catalogue without saying which run it is for"

    shim = (ROOT / "tools" / "static_shim.js").read_text(encoding="utf-8")
    assert "data/runs/${run}/options.json" in shim, \
        "the hosted demo serves the union to every run again"


def test_one_status_vocabulary_across_every_surface() -> None:
    """The RAG chip's words live in `viz/theme.STATUS_LABEL` and nowhere else.

    They used to live in **four** places — `viz/theme.py`, `render/sections.py`,
    `render/deck.py` and `web/src/lib/format.ts` — as four verbatim copies. So
    when 5.3d found that `unscored` read "No target" and contradicted the
    Target column beside it (Revenue per Employee showed a target of €165.0K
    with a chip saying "No target"), correcting the word in the obvious place
    would have fixed one surface of four and left the PDF and the deck saying
    the old thing.

    The three Python surfaces import it now. TypeScript cannot, so it is held
    in step here, the same arrangement as the design tokens and the
    table-to-KPI map.

    Mutations: restore either renderer's literal dict, or change the word in
    `theme.py` without changing `format.ts`.
    """
    import re

    from kpi_maker.viz.theme import STATUS_LABEL

    for module in ("kpi_maker/render/sections.py", "kpi_maker/render/deck.py",
                   "kpi_maker/render/dashboard.py"):
        text = (ROOT / module).read_text(encoding="utf-8")
        assert '"unscored":' not in text, (
            f"{module} keeps its own copy of the status words; import "
            f"STATUS_LABEL from viz.theme instead")

    ts = (ROOT / "web/src/lib/format.ts").read_text(encoding="utf-8")
    block = re.search(r"STATUS_LABEL[^{]*\{(.*?)\}", ts, re.S)
    assert block, "web/src/lib/format.ts has no STATUS_LABEL"
    pairs = dict(re.findall(r"(\w+):\s*'([^']*)'", block.group(1)))
    assert pairs == STATUS_LABEL, (
        f"the front end and the engine disagree about the status words:\n"
        f"  engine: {STATUS_LABEL}\n  web:    {pairs}")


def test_a_status_of_unscored_does_not_claim_there_is_no_target() -> None:
    """`KPI.status` returns "unscored" when a metric has a value and no
    *threshold* to judge it against — a statement about alert bands, which
    says nothing at all about targets.

    Its word was "No target", which was merely vague until 5.3d made the
    Target column legible and it became self-contradicting on the same row.

    Mutation: put "No target" back.
    """
    from kpi_maker.viz.theme import STATUS_LABEL

    assert "target" not in STATUS_LABEL["unscored"].lower(), STATUS_LABEL


def test_every_ci_job_has_a_timeout() -> None:
    """A job with no `timeout-minutes` inherits GitHub's **six-hour** default.

    That is the failure 0.8 exists to contain — kaleido's untimed
    `readline()` parks a Windows job forever, and a job that never finishes is
    indistinguishable from a slow one until somebody subtracts two
    timestamps. 0.8 capped the job it was debugging and left `lint` and
    `artifacts` uncapped, and `artifacts` is the one that renders every
    sample's PNGs — the job most exposed to that exact hang.

    Asserted as the property rather than by naming the jobs, so a sixth job
    added later cannot arrive without one.

    Mutation: delete any `timeout-minutes:` line from `ci.yml`.
    """
    import re

    text = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    body = text.split("\njobs:\n", 1)[1]

    # Job keys are the two-space-indented mapping keys under `jobs:`.
    jobs = re.findall(r"^  ([a-z][a-z0-9_-]*):\s*$", body, re.M)
    assert len(jobs) >= 5, jobs

    blocks = re.split(r"^  (?=[a-z][a-z0-9_-]*:\s*$)", body, flags=re.M)
    uncapped = [
        name for name, block in zip(jobs, blocks[1:])
        if not re.search(r"^\s+timeout-minutes:\s*\d+", block, re.M)
    ]
    assert not uncapped, (
        f"these CI jobs inherit GitHub's six-hour default: {uncapped}")


def test_the_test_job_leaves_room_for_the_suite_it_runs() -> None:
    """The Windows cap is calibrated at roughly twice the slowest observed
    Windows run, and the value is only defensible while that stays true.

    Measured across runs 54-56 the slowest Windows Test step was 9m40s, which
    was **81% of the old 12-minute cap** — and 0.8's justification for that 12
    read "Ubuntu and macOS finish the suite in ~40s", a figure that had gone
    stale by almost an order of magnitude. Crossing the cap kills the job with
    no output, so the drift would have surfaced as a phantom hang.

    This cannot check CI timings from here, so it checks the two things it
    can: the cap is at least the headroom that measurement implied, and the
    comment still carries the numbers it was derived from rather than a bare
    value nobody can re-check.

    Mutation: drop the cap back to 12, or delete the measurement table.
    """
    import re

    text = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    test_block = text.split("\n  test:\n", 1)[1].split("\n  smoke:", 1)[0]

    cap = re.search(r"^\s+timeout-minutes:\s*(\d+)", test_block, re.M)
    assert cap, "the test job lost its timeout"
    assert int(cap.group(1)) >= 18, (
        f"timeout-minutes is {cap.group(1)}; the slowest measured Windows run "
        f"was 9m40s and the rule is roughly 2x that")

    # At least three, because "roughly twice the slowest observed" is a claim
    # about a trend and one reading cannot support it. The first version of
    # this asserted a single row and stayed green when two of the three were
    # deleted -- a weak mutation, but the test was the weaker half.
    rows = re.findall(r"run \d+\s+Windows (\d+)m(\d+)s", test_block)
    assert len(rows) >= 3, (
        f"only {len(rows)} measurement(s) left in the comment; the cap's "
        f"justification is a trend, and this is how the previous value "
        f"outlived its own")
    slowest = max(int(m) * 60 + int(sec) for m, sec in rows)
    assert int(cap.group(1)) * 60 >= 1.8 * slowest, (
        f"cap {cap.group(1)}m is not ~2x the slowest recorded run "
        f"({slowest / 60:.1f}m)")


def test_the_move_formatter_agrees_across_the_two_languages() -> None:
    """`fmt.fmt_move` and `format.ts`'s `fmtMove` are one rule in two
    languages, held in step here because TypeScript cannot import from the
    engine — the same arrangement as STATUS_LABEL and the design tokens.

    **The rule is worth pinning because getting it wrong shipped.** A `pct`
    metric moves in *points* and its values arrive as fractions, so the
    version that lived inline in `render/dashboard._stat_tile` printed a
    4.4-point move in gross margin as "0.0 pts" — every percentage tile on
    every dashboard, understated a hundredfold, reading as a plausible "barely
    moved". It surfaced only when the rule was extracted and run against
    inputs whose answer was known.

    Mutations: drop the `* 100` on either side; change one side's rounding.
    """
    import shutil
    import subprocess

    from kpi_maker.fmt import fmt_move

    cases = [
        (0.758, 0.689, "pct"), (0.329, 0.285, "pct"),
        (0.108, 0.054, "pct"), (0.183, 0.208, "pct"),
        (158_300.0, 154_200.0, "currency"), (240.0, 225.0, "count"),
        (12.5, 10.0, "ratio"), (0.048, 0.026, "pct"),
    ]
    expected = [fmt_move(c, p, u) for c, p, u in cases]
    # The bug this pins, stated outright rather than left to the comparison.
    assert expected[0] == "6.9 pts", expected[0]
    assert expected[1] == "4.4 pts", expected[1]

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is absent; the three-OS matrix does not install it")

    source = (ROOT / "web/src/lib/format.ts").read_text(encoding="utf-8")
    body = re.search(r"export function fmtMove\((.*?)\n\}", source, re.S)
    assert body, "format.ts has no fmtMove"
    script = (
        source[body.start():body.end()]
        .replace("export function", "function")
        .replace(": number | null | undefined", "")
        .replace(": string | null", "")
        .replace("): string | null {", ") {")
        + "\nconsole.log(JSON.stringify("
        + json.dumps(cases)
        + ".map(function (c) { return fmtMove(c[0], c[1], c[2]); })));"
    )
    out = subprocess.run([node, "-e", script], capture_output=True, text=True,
                         check=True).stdout
    assert json.loads(out) == expected, (json.loads(out), expected)
