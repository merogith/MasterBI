"""Storage for user-defined KPIs.

Two places a custom KPI can live, for two different intents:

  * `spec.metrics.custom` — this run only. Handled by the selection engine.
  * `kpi/library/user/*.yaml` — kept, and offered on every future run. This
    module owns that directory.

One file per KPI, named after its id. A directory of small files diffs and
merges cleanly, and a broken one takes out a single metric rather than the
whole store.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Tuple

import yaml

from .schema import KPI

USER_DIR = Path(__file__).parent / "library" / "user"


def _path_for(kpi_id: str) -> Path:
    # The id is used as a filename, so it must not be able to escape the
    # directory. Ids are slugified on creation, but this store is also reachable
    # over HTTP and must not trust that.
    safe = "".join(c for c in kpi_id if c.isalnum() or c in "_-")
    if not safe or safe != kpi_id:
        raise ValueError(
            f"invalid KPI id {kpi_id!r} — letters, digits, underscore and hyphen only")
    return USER_DIR / f"{safe}.yaml"


def load_user_kpis() -> Tuple[List[KPI], Dict[str, str]]:
    """Every stored user KPI, plus a reason for each one that failed to load.

    A malformed file must not break the whole product. The bad entry is
    reported by id so the studio can show "this KPI could not be loaded" next to
    the others rather than the app refusing to start.
    """
    if not USER_DIR.exists():
        return [], {}

    kpis: List[KPI] = []
    broken: Dict[str, str] = {}
    for path in sorted(USER_DIR.glob("*.yaml")):
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not raw:
                continue
            entries = raw if isinstance(raw, list) else [raw]
            for entry in entries:
                kpis.append(KPI(**entry))
        except Exception as exc:                        # noqa: BLE001
            broken[path.stem] = str(exc)
    return kpis, broken


def save_user_kpi(kpi: KPI) -> Path:
    USER_DIR.mkdir(parents=True, exist_ok=True)
    path = _path_for(kpi.id)
    payload = kpi.model_dump(mode="json", exclude_none=True)
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path


def delete_user_kpi(kpi_id: str) -> bool:
    path = _path_for(kpi_id)
    if not path.exists():
        return False
    path.unlink()
    return True


def user_kpi_ids() -> List[str]:
    kpis, _ = load_user_kpis()
    return sorted(k.id for k in kpis)
