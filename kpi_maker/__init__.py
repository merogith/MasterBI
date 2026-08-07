"""KPI Dashboard Maker — profile-driven KPI selection, data, dashboards and reports.

The pipeline is deterministic end to end:

    CompanyProfile -> KPI selection -> data -> metrics -> facts -> artifacts

Modes (Samples / Survey / AI Builder) differ ONLY in how the CompanyProfile is
produced. See ARCHITECTURE.md.
"""

__version__ = "0.1.0"
