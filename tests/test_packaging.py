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
