"""Aligned plain-text table, shared by `freemodels list` and `/freemodels`.

Plain text on purpose — no box drawing, no ANSI, no Rich markup. Hermes
renders a plugin slash-command result through ``ANSI(...)``, so escapes would
survive, but plain output stays copy-pasteable, greppable through a pipe, and
identical in a chat pane and a terminal.

Rows come from the snapshot dicts ``cli._snapshot_row`` builds — the same
shape the ``--json`` payload uses and the same shape state.json stores — so
the table and the JSON can never drift apart.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from typing import Any

COLUMNS = ("Rank", "Model", "Privacy", "Served by", "Uptime", "Status")
_ALIGN = {
    "Rank": ">", "Model": "<", "Privacy": "<",
    "Served by": "<", "Uptime": ">", "Status": "<",
}
# Model is absent: it flexes into whatever the other columns leave behind.
_CAPS = {"Rank": 5, "Privacy": 8, "Served by": 18, "Uptime": 6, "Status": 26}

MIN_MODEL_WIDTH = 24
MIN_TOTAL, MAX_TOTAL = 72, 120
_SEP = "  "

EMPTY_HINT = "No models to show — run `hermes freemodels sync`."


@dataclass
class Row:
    rank: str
    model: str
    tier: str
    via: str
    uptime: str
    status: str

    def cells(self) -> tuple[str, ...]:
        return (self.rank, self.model, self.tier, self.via, self.uptime, self.status)


def terminal_width() -> int:
    return shutil.get_terminal_size(fallback=(100, 24)).columns


def _ellipsize(s: str, n: int) -> str:
    """Right-truncate — for fields whose head carries the meaning."""
    if n <= 0:
        return ""
    return s if len(s) <= n else s[: n - 1] + "…"


def _ellipsize_middle(s: str, n: int) -> str:
    """Truncate the middle, keeping both ends.

    Model IDs are load-bearing at both ends: the vendor prefix says who serves
    it and the ``:free`` suffix says it's the free variant. Dropping either
    makes the row ambiguous, so cut the middle instead.
    """
    if n <= 0:
        return ""
    if len(s) <= n:
        return s
    if n == 1:
        return "…"
    keep = n - 1
    head = (keep + 1) // 2
    tail = keep - head
    return s[:head] + "…" + (s[-tail:] if tail else "")


# Display labels for the internal tier slugs. The slugs stay lowercase in
# state.json and --json (they're an API contract); only the table shows these.
TIER_LABELS = {
    "private": "Private",
    "logs": "Logs",
    "trains": "Trains",
    "unknown": "Unknown",
}


def _status_for(d: dict[str, Any]) -> str:
    """Derive STATUS from structured fields only — never by parsing `reason`."""
    outcome = d.get("outcome") or ""

    if outcome == "selected":
        idx = d.get("role_index")
        return "← Default" if idx == 0 else f"← Fallback #{idx}"

    if outcome == "catalog":
        return {
            "private": "",
            "logs": "Retains prompts",
            "trains": "Trains on prompts",
        }.get(d.get("tier") or "", "Privacy unknown")

    if outcome == "degraded":
        up = d.get("uptime_1d")
        return f"Endpoint down ({up:.0f}%)" if isinstance(up, (int, float)) else "Endpoint down"

    if outcome == "unclassified":
        return "Not classified"

    # Skipped — the three hard filters, in the order select_models applies them.
    if d.get("tools") is False:
        return "No tool support"
    if d.get("expires"):
        return f"Expires {d['expires']}"
    return "No free endpoint"


def row_from_snapshot(d: dict[str, Any]) -> Row:
    """Build a Row from a snapshot dict, tolerating keys an older state.json lacks."""
    rank = d.get("rank")
    up = d.get("uptime_1d")
    tier = d.get("tier")
    return Row(
        rank=f"#{rank}" if rank else "—",
        model=str(d.get("id") or "?"),
        tier=TIER_LABELS.get(tier, str(tier).title()) if tier else "—",
        via=str(d.get("endpoint_provider") or "—"),
        uptime=f"{up:.0f}%" if isinstance(up, (int, float)) else "—",
        status=_status_for(d),
    )


def _widths(rows: list[Row], width: int | None) -> list[int]:
    natural = [
        max([len(col)] + [len(r.cells()[i]) for r in rows])
        for i, col in enumerate(COLUMNS)
    ]
    capped = [
        min(n, _CAPS[col]) if col in _CAPS else n
        for n, col in zip(natural, COLUMNS)
    ]

    model_idx = COLUMNS.index("Model")
    total = width if width is not None else terminal_width()
    total = max(MIN_TOTAL, min(total, MAX_TOTAL))
    fixed = sum(w for i, w in enumerate(capped) if i != model_idx)
    budget = max(MIN_MODEL_WIDTH, total - fixed - len(_SEP) * (len(COLUMNS) - 1))
    # Shrink to fit the data too — a 3-row table shouldn't be padded to 120 cols.
    capped[model_idx] = min(capped[model_idx], budget)
    return capped


def render_table(rows: list[Row], *, width: int | None = None, header: bool = True) -> str:
    if not rows:
        return EMPTY_HINT

    widths = _widths(rows, width)
    model_idx = COLUMNS.index("Model")

    def line(cells: tuple[str, ...]) -> str:
        out = []
        for i, (cell, w) in enumerate(zip(cells, widths)):
            trimmed = _ellipsize_middle(cell, w) if i == model_idx else _ellipsize(cell, w)
            out.append(f"{trimmed:{_ALIGN[COLUMNS[i]]}{w}}")
        # rstrip so an empty trailing STATUS leaves no ragged whitespace.
        return _SEP.join(out).rstrip()

    out: list[str] = []
    if header:
        head = line(COLUMNS)
        out.append(head)
        out.append("-" * len(head))  # match the rstripped header, not the raw width
    out.extend(line(r.cells()) for r in rows)
    return "\n".join(out)


def render_snapshots(
    snapshots: list[dict[str, Any]], *, width: int | None = None, header: bool = True
) -> str:
    """Convenience: snapshot dicts straight to a rendered table."""
    return render_table(
        [row_from_snapshot(d) for d in snapshots], width=width, header=header
    )
