"""Table renderer shared by `freemodels list` and `/freemodels`."""

from hermes_openrouter_free_rotator.render import (
    COLUMNS,
    EMPTY_HINT,
    MIN_MODEL_WIDTH,
    _ellipsize_middle,
    render_snapshots,
    render_table,
    row_from_snapshot,
)


def _snap(**kw):
    base = {
        "rank": 1,
        "id": "tencent/hy3:free",
        "tier": "private",
        "tools": True,
        "expires": None,
        "uptime_1d": 99.0,
        "endpoint_provider": "Chutes",
        "reason": "",
        "outcome": "catalog",
        "role_index": None,
        "in_catalog": True,
        "available": True,
    }
    base.update(kw)
    return base


def test_render_table_exact_output():
    rows = [
        _snap(rank=1, id="tencent/hy3:free", outcome="selected", role_index=0),
        _snap(rank=2, id="cohere/north-mini-code:free", outcome="selected", role_index=1,
              endpoint_provider="Cohere", uptime_1d=100.0),
        _snap(rank=3, id="nvidia/nemotron:free", tier="logs",
              endpoint_provider="NVIDIA", uptime_1d=94.0),
    ]
    expected = "\n".join([
        "Rank  Model                        Privacy  Served by  Uptime  Status",
        "---------------------------------------------------------------------",
        "  #1  tencent/hy3:free             Private  Chutes        99%  ← Default",
        "  #2  cohere/north-mini-code:free  Private  Cohere       100%  ← Fallback #1",
        "  #3  nvidia/nemotron:free         Logs     NVIDIA        94%  Retains prompts",
    ])
    assert render_snapshots(rows, width=90) == expected


def test_rule_matches_header_width():
    rows = [_snap(rank=i, id=f"vendor/model-{i}:free") for i in range(1, 4)]
    out = render_snapshots(rows, width=90).splitlines()
    assert len(out[0]) == len(out[1])
    assert set(out[1]) == {"-"}


def test_model_id_middle_ellipsis_keeps_both_ends():
    long_id = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"
    out = _ellipsize_middle(long_id, 24)
    assert len(out) == 24
    assert "…" in out
    assert out.startswith("nvidia/")
    assert out.endswith(":free")


def test_ellipsize_middle_passes_short_strings_through():
    assert _ellipsize_middle("abc", 10) == "abc"


def test_status_shows_default_and_fallback_roles():
    assert row_from_snapshot(_snap(outcome="selected", role_index=0)).status == "← Default"
    assert row_from_snapshot(_snap(outcome="selected", role_index=2)).status == "← Fallback #2"


def test_pinned_status_outranks_every_other_label():
    # The pin is the one thing here the user chose, and it explains why the
    # row isn't rotating — so it wins over role and tier alike.
    assert row_from_snapshot(_snap(pinned=True, outcome="selected", role_index=0)).status == (
        "← Pinned"
    )
    assert row_from_snapshot(_snap(pinned=True, tier="trains")).status == "← Pinned"
    # Absent or false is the normal case and must not change anything.
    assert row_from_snapshot(_snap(pinned=False, tier="logs")).status == "Retains prompts"


def test_status_shows_tier_labels():
    assert row_from_snapshot(_snap(tier="private")).status == ""
    assert row_from_snapshot(_snap(tier="logs")).status == "Retains prompts"
    assert row_from_snapshot(_snap(tier="trains")).status == "Trains on prompts"
    assert row_from_snapshot(_snap(tier="unknown")).status == "Privacy unknown"


def test_privacy_column_is_title_cased():
    # The slug stays lowercase in state.json / --json; only the table title-cases.
    assert row_from_snapshot(_snap(tier="private")).tier == "Private"
    assert row_from_snapshot(_snap(tier="trains")).tier == "Trains"
    assert row_from_snapshot(_snap(tier=None)).tier == "—"


def test_status_shows_skip_reasons():
    down = _snap(outcome="degraded", uptime_1d=0.0, tier=None)
    assert row_from_snapshot(down).status == "Endpoint down (0%)"

    assert row_from_snapshot(_snap(outcome="unclassified")).status == "Not classified"

    no_tools = _snap(outcome="skipped", tools=False, rank=None)
    assert row_from_snapshot(no_tools).status == "No tool support"

    expiring = _snap(outcome="skipped", expires="2026-08-20", rank=None)
    assert row_from_snapshot(expiring).status == "Expires 2026-08-20"

    gone = _snap(outcome="skipped", rank=None)
    assert row_from_snapshot(gone).status == "No free endpoint"


def test_narrow_terminal_floors_model_column():
    # Every other column at its cap, so MODEL's share would go negative and
    # the floor has to take over. Widths below MIN_TOTAL clamp up to it.
    rows = [
        _snap(
            id="nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
            endpoint_provider="A-Very-Long-Provider-Name",
            tier="trains",
        )
    ]
    out = render_snapshots(rows, width=40).splitlines()
    header = out[0]
    model_start = header.index("Model")
    model_width = header.index("Privacy") - model_start - len("  ")
    assert model_width == MIN_MODEL_WIDTH
    # ...and nothing overflows its column.
    assert out[2][model_start : model_start + model_width].strip().endswith(":free")


def test_row_from_snapshot_tolerates_missing_keys():
    # A v1 state.json row, written before the catalog fields existed.
    row = row_from_snapshot({"rank": 3, "id": "a/b:free", "tier": "private"})
    assert row.rank == "#3" and row.via == "—" and row.uptime == "—"


def test_unranked_row_shows_dashes():
    assert row_from_snapshot(_snap(rank=None)).rank == "—"


def test_render_empty_rows_returns_hint():
    assert render_table([]) == EMPTY_HINT


def test_header_can_be_omitted():
    out = render_snapshots([_snap()], width=90, header=False)
    assert COLUMNS[0] not in out
    assert len(out.splitlines()) == 1
