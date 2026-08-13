"""Run the frozen executable and make it produce a board pack.

    python installer/smoke.py dist/MasterBI

A build that succeeds proves the files were collected. It does not prove the
app starts, that the bundled KPI library is readable, or that kaleido's native
subprocess survived freezing — and that last one is the risk this project has
already been bitten by twice, on Windows, where it needs a non-ASCII path
workaround to run at all.

The failure this is really for is quiet: a run that finishes "successfully"
with no chart PNGs, so the PDF and the deck ship with every exhibit missing.
The Pages workflow already treats a missing `charts/*.png` as a portability
canary; the same canary belongs here, where the binary is the deliverable.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

PORTS = (8000, 8001, 8080, 8765)
REQUIRED = ("dashboard.html", "report.pdf", "deck.pptx", "report.docx",
            "workbook.xlsx", "facts.csv", "findings.json", "profile.json",
            "kpi_set.json")


def _get(url: str, timeout: float = 10.0):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read())


def _post(url: str, payload: dict):
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def _find_port(process: subprocess.Popen, deadline: float) -> str:
    """The app picks its own port, so discover it rather than assume it."""
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise SystemExit(f"the executable exited early ({process.returncode})")
        for port in PORTS:
            with socket.socket() as probe:
                probe.settimeout(0.3)
                if probe.connect_ex(("127.0.0.1", port)) != 0:
                    continue
            base = f"http://127.0.0.1:{port}"
            try:
                if _get(f"{base}/api/health", timeout=3).get("status") == "ok":
                    return base
            except (urllib.error.URLError, OSError, ValueError):
                continue
        time.sleep(1)
    raise SystemExit("the executable never became healthy")


def main(binary: Path) -> int:
    data = Path(tempfile.mkdtemp(prefix="masterbi-smoke-"))
    env = {
        **os.environ,
        # Never the developer's or the runner's real data directory, and it
        # also proves the override a portable install depends on works.
        "MASTERBI_DATA_DIR": str(data),
        # A browser on a CI runner is at best useless and at worst a hang.
        "BROWSER": "true",
    }
    process = subprocess.Popen([str(binary)], env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True)
    try:
        base = _find_port(process, time.monotonic() + 180)
        print(f"up at {base}")

        # The bundled read-only data: samples and the KPI library. Both are
        # `Path(__file__).parent`-relative in a checkout and come from the
        # unpacked bundle when frozen, so an empty answer here means the spec
        # failed to collect them.
        samples = _get(f"{base}/api/samples")
        assert samples, "no samples in the bundle"
        catalog = _get(f"{base}/api/catalog/kpis")
        assert catalog.get("kpis"), "no KPI library in the bundle"
        print(f"{len(samples)} samples, {len(catalog['kpis'])} KPIs")

        run = _post(f"{base}/api/runs",
                    {"mode": "sample", "sample_id": samples[0]["id"]})
        run_id = run["run_id"]
        print(f"running {run_id} — {run.get('company')}")

        deadline = time.monotonic() + 900
        status = "queued"
        while time.monotonic() < deadline:
            state = _get(f"{base}/api/runs/{run_id}")
            status = state.get("status")
            if status in ("done", "error", "cancelled"):
                break
            time.sleep(3)

        if status != "done":
            state = _get(f"{base}/api/runs/{run_id}")
            raise SystemExit(f"run {status}: {state.get('error')}")

        produced = {a["name"] for a in _get(
            f"{base}/api/runs/{run_id}")["summary"]["artifacts"]}
        missing = [name for name in REQUIRED if name not in produced]
        if missing:
            raise SystemExit(f"missing artifacts: {missing}")

        charts = list((data / "runs" / run_id / "charts").glob("*.png"))
        if not charts:
            raise SystemExit(
                "no chart PNGs — kaleido did not survive freezing. The PDF and "
                "the deck would ship with every exhibit missing.")

        # Written where the user can find them again, not into the bundle's
        # temporary unpack directory, which the OS deletes on exit.
        assert (data / "runs" / run_id).is_dir(), "runs did not reach the data dir"

        print(f"OK — {len(produced)} artifacts, {len(charts)} chart PNGs, "
              f"runs under {data}")
        return 0
    finally:
        process.terminate()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    raise SystemExit(main(Path(sys.argv[1]).resolve()))
