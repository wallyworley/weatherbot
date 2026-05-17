"""Cron/ad-hoc wrapper for execution-quality research report."""
import argparse
from pathlib import Path

from research import execution_quality


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days-back", type=int, default=45)
    parser.add_argument("--out-dir", type=Path, default=Path("research/reports"))
    args = parser.parse_args()
    result = execution_quality.run(days_back=args.days_back, out_dir=args.out_dir)
    print(Path(result["report_path"]).read_text())
