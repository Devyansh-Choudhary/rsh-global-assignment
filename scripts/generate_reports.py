"""Generate Monday's deliverables without opening the app.

Usage:
    python scripts/generate_reports.py                 # report for 2026-11-16
    python scripts/generate_reports.py --date 2026-10-19 --no-backtest
    python scripts/generate_reports.py --data-dir path/to/csvs --out outputs/
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from src.engine import load_clean, run_backtest, run_engine  # noqa: E402
from src.report_generator import action_list, build_pdf, whatsapp_messages  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", default=config.DEFAULT_REPORT_DATE, help="Report Monday (YYYY-MM-DD)")
    ap.add_argument("--data-dir", default=str(config.DATA_DIR))
    ap.add_argument("--out", default=str(config.OUTPUT_DIR))
    ap.add_argument("--no-backtest", action="store_true", help="Skip the week-by-week replay (faster)")
    a = ap.parse_args()

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    clean = load_clean(a.data_dir)
    res = run_engine(clean, a.date)
    tag = res.report_date.strftime("%Y-%m-%d")
    bt_summary = None
    if not a.no_backtest:
        log, bt_summary = run_backtest(clean, res.report_date)
        bt_summary.to_csv(out / f"backtest_summary_{tag}.csv", index=False)
        log.to_csv(out / f"backtest_weekly_log_{tag}.csv", index=False)

    (out / f"joy_monday_report_{tag}.pdf").write_bytes(build_pdf(res, bt_summary, clean.profile))
    action_list(res).to_csv(out / f"action_list_{tag}.csv", index=False, encoding="utf-8-sig")
    msgs = whatsapp_messages(res)
    (out / f"whatsapp_messages_{tag}.txt").write_text(
        "\n\n" + ("\n\n" + "-" * 60 + "\n\n").join(f"[{team}]\n{m}" for team, m in msgs.items()) + "\n",
        encoding="utf-8")
    cols = ["priority", "incident_id", "severity", "score", "status", "confidence", "team", "platform", "city",
            "warehouse_id", "n_skus", "start_week", "weeks_active", "stores_hit", "n_stores", "risk_low", "risk_point",
            "risk_high", "lost_low", "lost_point", "lost_high", "recoverable_next_week", "title", "recommended_action"]
    res.incidents[cols].to_csv(out / f"all_incidents_{tag}.csv", index=False, encoding="utf-8-sig")
    clean.log.to_frame().to_csv(out / "data_cleaning_log.csv", index=False)

    print(f"Report for Monday {tag} written to {out.resolve()} in {time.time() - t0:.1f}s")
    for r in res.priority.itertuples():
        print(f"  #{r.priority} [{r.severity}] {r.title}  ->  {r.team}")


if __name__ == "__main__":
    main()
