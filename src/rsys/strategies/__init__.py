"""`strategies` package: independently replaceable strategy modules (docs/M2_IMPLEMENTATION_HANDOFF.md
steps 7-8). Each strategy produces `rsys.orders.Proposal` objects only; sizing, acceptance
and execution remain external (`rsys.sizing`/`rsys.acceptance`/`rsys.fixture_engine`).
"""
