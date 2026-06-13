#!/usr/bin/env python3
"""Re-cost estimated run records from token counts using the canonical price table.

Reviews run in subscription/CLI mode report no real billed cost, so review.py estimated
cost_usd from a PRICES table — which had three stale, wrong entries (claude-opus-4-8 at
$15/$75, gpt-5.5 at $1.25/$10, minimax at $0.60/$2.40). This recomputes cost_usd from the
immutable token usage times the corrected prices in schema/prices.json, preserving the
original under cost_usd_legacy. Records with cost_estimated == False carry a real
provider-reported cost and are left untouched. Round records' `cost` is re-summed from
their member runs. Idempotent: re-running changes nothing once recosted.

    python3 scripts/recost.py
"""
import json
import pathlib

import tcdata

PRICES = {k: tuple(v) for k, v in
          json.loads((tcdata.ROOT / "schema" / "prices.json").read_text()).items()
          if not k.startswith("_")}


def recost_run(d):
    if d.get("cost_estimated") is False:          # real provider cost — never touch
        return None
    price = PRICES.get(d.get("model"))
    if not price:                                  # unpriced model — can't recompute
        return None
    u = d.get("usage") or {}
    new = (u.get("input_tokens", 0) * price[0] + u.get("output_tokens", 0) * price[1]) / 1e6
    new = round(new, 6)
    if abs(new - (d.get("cost_usd") or 0)) < 1e-9 and "cost_usd_legacy" in d:
        return None                                # already recosted to this value
    if "cost_usd_legacy" not in d:
        d["cost_usd_legacy"] = d.get("cost_usd")
    d["cost_usd"] = new
    d["cost_recosted"] = True
    return new


def main():
    runs_dir = tcdata.ROOT / "records" / "runs"
    new_cost = {}                                  # run_id -> corrected cost
    changed = 0
    for p in sorted(runs_dir.rglob("*.json")):
        d = json.loads(p.read_text())
        before = d.get("cost_usd")
        recost_run(d)
        new_cost[d["run_id"]] = d.get("cost_usd") or 0
        if d.get("cost_usd") != before:
            p.write_text(json.dumps(d, indent=2) + "\n")
            changed += 1

    # Re-sum round costs from corrected member-run costs (only when all members are known).
    rounds_changed = 0
    for p in sorted((tcdata.ROOT / "records" / "rounds").rglob("*.json")):
        d = json.loads(p.read_text())
        ids = d.get("run_ids") or []
        if not ids or not all(i in new_cost for i in ids):
            continue
        total = round(sum(new_cost[i] for i in ids), 6)
        if abs(total - (d.get("cost") or 0)) > 1e-9:
            if "cost_legacy" not in d:
                d["cost_legacy"] = d.get("cost")
            d["cost"] = total
            d["cost_recosted"] = True
            p.write_text(json.dumps(d, indent=2) + "\n")
            rounds_changed += 1

    print(f"recost: {changed} run records, {rounds_changed} round records updated")


if __name__ == "__main__":
    main()
