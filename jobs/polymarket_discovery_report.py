"""Cron/ad-hoc wrapper for read-only Polymarket weather discovery."""
import argparse
from pathlib import Path

from research import polymarket_discovery


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-pages", type=int, default=20)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--slug", action="append", default=[],
                        help="Explicit Polymarket event slug to include even if broad discovery misses it.")
    parser.add_argument("--out-dir", type=Path, default=Path("research/reports"))
    args = parser.parse_args()
    result = polymarket_discovery.run(
        max_pages=args.max_pages,
        limit=args.limit,
        out_dir=args.out_dir,
        slugs=args.slug,
    )
    print(Path(result["report_path"]).read_text())
