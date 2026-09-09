"""Batch-flow wrapper for attaching frame timestamps to merged sensor CSVs."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from stingray.images.add_media import add_media_to_merged


def workspace_path(work_dir, value):
    value = Path(value).expanduser()
    return value if value.is_absolute() else Path(work_dir) / value


def load_cruises(min_start_date, fallback_days):
    cruises = pd.read_csv("https://nes-lter-api.whoi.edu/api/ctd/cruises/get/all")
    for column in ("start_time", "end_time"):
        cruises[column] = pd.to_datetime(cruises[column], errors="coerce", utc=True).dt.tz_localize(None)
    cruises = cruises.dropna(subset=["start_time"])
    cruises = cruises[cruises["start_time"] >= pd.Timestamp(min_start_date)].sort_values("start_time")
    cruises["name"] = cruises["name"].astype(str).str.upper()
    cruises["end_time"] = cruises["end_time"].fillna(
        cruises["start_time"] + pd.Timedelta(days=fallback_days)
    )
    return [
        {"name": row.name, "start": row.start_time.strftime("%Y-%m-%d"), "end": row.end_time.strftime("%Y-%m-%d")}
        for row in cruises.itertuples(index=False)
    ]


def expand_template(value, cruise):
    return Path(str(value).format(cruise=cruise)).expanduser()


def find_merged_csv(merged_dir: Path, cruise: str) -> Path:
    matches = sorted(merged_dir.glob(f"*_{cruise}.csv"))
    if not matches:
        raise FileNotFoundError(f"No merged sensor CSV found for {cruise} in {merged_dir}")
    if len(matches) > 1:
        raise RuntimeError(f"Multiple merged sensor CSVs found for {cruise}: {matches}")
    return matches[0]


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", default=".")
    parser.add_argument("--merged-dir", default="dash_data/data/stingray")
    parser.add_argument("--out-dir", default="dash_data/data/media_enriched")
    parser.add_argument("--media-list-dirs", nargs="+", required=True, help="Frame-list files or {cruise} directory templates, in stream order.")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--cruise", nargs="+", help="Process only these cruises.")
    parser.add_argument("--fallback-days", type=int, default=7)
    args = parser.parse_args(argv)

    work_dir = Path(args.work_dir).expanduser().resolve()
    cruises = load_cruises("1900-01-01", args.fallback_days)
    if args.cruise:
        wanted = {name.upper() for name in args.cruise}
        available = {cruise["name"] for cruise in cruises}
        missing = sorted(wanted - available)
        if missing:
            parser.error(f"Cruise(s) not found: {', '.join(missing)}")
        cruises = [cruise for cruise in cruises if cruise["name"] in wanted]
    merged_dir = workspace_path(work_dir, args.merged_dir)
    out_dir = workspace_path(work_dir, args.out_dir)
    for cruise in cruises:
        merged_csv = find_merged_csv(merged_dir, cruise["name"])
        media_sources = [
            workspace_path(work_dir, expand_template(source, cruise["name"]))
            for source in args.media_list_dirs
        ]
        out_path = out_dir / merged_csv.name
        print(f"Adding media to {cruise['name']}: {merged_csv.name} -> {out_path}")
        add_media_to_merged(
            merged_csv=merged_csv,
            cruise=cruise["name"],
            media_list_dirs=[str(path) for path in media_sources],
            out_path=out_path,
            overwrite=args.overwrite,
        )


if __name__ == "__main__":
    main()
