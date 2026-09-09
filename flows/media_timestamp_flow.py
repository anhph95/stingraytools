"""Batch-flow wrapper for generating frame timestamps for each cruise."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from stingray.images.build_frame_timestamps import main as frame_timestamp_main


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


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", default=".")
    parser.add_argument("--media-dir-template", required=True, help="Media directory template containing {cruise}.")
    parser.add_argument("--out-dir-template", default="media_list/CAMERA_STREAM_1")
    parser.add_argument("--max-workers", type=int, default=None)
    parser.add_argument("--file-limit", type=int, default=None)
    parser.add_argument("--suffix", nargs="+")
    parser.add_argument("--details", action="store_true")
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
    for cruise in cruises:
        media_dir = workspace_path(work_dir, expand_template(args.media_dir_template, cruise["name"]))
        out_dir = workspace_path(work_dir, expand_template(args.out_dir_template, cruise["name"]))
        print(f"Processing media timestamps: {cruise['name']} from {media_dir}")
        command = ["--cruise", cruise["name"], "--media-dir", str(media_dir), "--out-dir", str(out_dir), "--work-dir", str(work_dir)]
        if args.max_workers is not None:
            command.extend(["--max-workers", str(args.max_workers)])
        if args.file_limit is not None:
            command.extend(["--file-limit", str(args.file_limit)])
        if args.suffix:
            command.extend(["--suffix", *args.suffix])
        if args.details:
            command.append("--details")
        frame_timestamp_main(command)


if __name__ == "__main__":
    main()
