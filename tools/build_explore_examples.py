"""Freeze the same general-data demos and exports served by the local API."""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from kpi_maker.explore import api
from kpi_maker.explore.engine import Analysis
from kpi_maker.explore.exports import export


def build(site: Path):
    projects = []
    with (
        tempfile.TemporaryDirectory() as temp,
        patch.object(api, "root", return_value=Path(temp)),
    ):
        for kind in ("retail", "saas", "pokemon"):
            project = api.demo(kind)
            project["id"] = hashlib.sha256(kind.encode()).hexdigest()[:12]
            directory = site / "data" / "explore"
            directory.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(project, ensure_ascii=False, allow_nan=False)
            (directory / f"{kind}.json").write_text(payload, encoding="utf-8")
            (directory / f"{project['id']}.json").write_text(payload, encoding="utf-8")
            files = site / "files" / "explore" / project["id"]
            files.mkdir(parents=True, exist_ok=True)
            for kind_out in ("pdf", "pptx", "html", "csv"):
                content, _ = export(
                    Analysis.model_validate(project["spec"]),
                    project["result"],
                    kind_out,
                    True,
                )
                (files / f"report.{kind_out}").write_bytes(content)
            projects.append({"id": project["id"], "title": project["spec"]["title"]})
        (site / "data" / "explore" / "projects.json").write_text(
            json.dumps(projects), encoding="utf-8"
        )
