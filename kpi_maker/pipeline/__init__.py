"""The staged, cacheable pipeline.

`run_pipeline` used to be one function that always did everything. This package
turns it into a dependency graph of named stages so a caller can change one
thing and re-run only what that change actually affects.
"""
from .graph import STAGES, Stage, stage  # noqa: F401
from .runner import RunContext, execute, plan_rerun  # noqa: F401

__all__ = ["STAGES", "Stage", "stage", "RunContext", "execute", "plan_rerun"]
