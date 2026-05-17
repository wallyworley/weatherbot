"""Generate an AI-readable station/date context brief."""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from research import ai_context_brief


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--station", required=True)
    parser.add_argument("--valid-date", type=date.fromisoformat, required=True)
    parser.add_argument("--out-dir", type=Path, default=Path("research/reports"))
    args = parser.parse_args()
    result = ai_context_brief.run(args.station, args.valid_date, args.out_dir)
    print(result["text"])
