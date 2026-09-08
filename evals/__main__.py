"""`python -m evals [suite]` — run the scored suites and report rates."""
from __future__ import annotations

import sys

from evals.harness import report, run


def main() -> int:
    suite = sys.argv[1] if len(sys.argv) > 1 else None
    return report(run(suite))


if __name__ == "__main__":
    raise SystemExit(main())
