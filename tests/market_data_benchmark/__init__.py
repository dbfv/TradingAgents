"""The Yahoo Finance vs DNSE OpenAPI benchmark for the VN30 index.

Read-only. Nothing in this package places, modifies or cancels an order, and
nothing here reads an account, a balance or a position. It compares two
read-only daily market-data sources and reports what the evidence shows.

The pieces fit together as:

    universe.py    the dated VN30 snapshot
    normalization.py  symbol / date / unit normalisation shared by both sides
    calendars.py   the trading-session calendar, derived and then validated
    providers/     one adapter per source, both returning the same shapes
    metrics.py     coverage, field metrics, mismatches, stability
    report.py      the artifacts
    run_benchmark.py the entry point
"""
