"""`python -m kpi_maker.authoring lint [--all | --pack NAME ...]`

The command a pack author runs before opening a pull request, and the one CI
runs to keep the library honest. Exit code 1 on any error, so it works as a
gate without anyone having to read the output.
"""
from __future__ import annotations

import argparse
import sys
from typing import List

from .lint import lint_all, lint_group


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m kpi_maker.authoring")
    sub = parser.add_subparsers(dest="command", required=True)

    lint = sub.add_parser("lint", help="check the KPI packs")
    lint.add_argument("--all", action="store_true",
                      help="every load group a profile can produce")
    lint.add_argument("--pack", action="append", default=[],
                      help="lint these packs as one group (repeatable)")
    lint.add_argument("--quiet", action="store_true",
                      help="print only the summary lines and any errors")

    args = parser.parse_args(argv)
    if args.command != "lint":
        parser.error("unknown command")

    if args.pack:
        reports = [lint_group(args.pack)]
    elif args.all:
        reports = lint_all()
    else:
        parser.error("pass --all or --pack NAME")

    errors = 0
    for report in reports:
        print(f"\n{report.summary()}")
        for finding in report.findings:
            if args.quiet and finding.level != "error":
                continue
            print(f"  {finding}")
        errors += len(report.errors)

    print(f"\n{len(reports)} group(s) linted, {errors} error(s)")
    return 1 if errors else 0


if __name__ == "__main__":                                # pragma: no cover
    sys.exit(main())
