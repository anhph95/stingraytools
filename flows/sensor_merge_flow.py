"""Batch-flow wrapper for merging one or more cruises' sensor data."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from stingray.sensors.merge import merge_sensors

CALIBRATION_CUTOVER_DATE = pd.Timestamp("2021-08-19")


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
        {
            "name": row.name,
            "start": row.start_time.strftime("%Y-%m-%d"),
            "end": row.end_time.strftime("%Y-%m-%d"),
            "cal_year": "2019" if row.start_time < CALIBRATION_CUTOVER_DATE else "2021",
        }
        for row in cruises.itertuples(index=False)
    ]


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", default=".")
    parser.add_argument("--root", default="sensor_data")
    parser.add_argument("--out-dir", default="dash_data/data/stingray")
    parser.add_argument("--index-dir", default="indexes")
    parser.add_argument("--time-bin-seconds", type=float, default=5.0)
    parser.add_argument("--suna-cal-dir", default="suna_calibration")
    parser.add_argument("--overwrite-index", action="store_true")
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
        message = f"Processing sensor merge: {cruise['name']} {cruise['start']} through {cruise['end']}"
        print(message)
        merge_sensors(
            cruise=cruise["name"],
            start=cruise["start"],
            end=cruise["end"],
            root=workspace_path(work_dir, args.root),
            cal_year=cruise["cal_year"],
            time_bin_seconds=args.time_bin_seconds,
            out_dir=workspace_path(work_dir, args.out_dir),
            index_dir=workspace_path(work_dir, args.index_dir),
            overwrite_index=args.overwrite_index,
            suna_cal_dir=workspace_path(work_dir, args.suna_cal_dir),
        )


if __name__ == "__main__":
    main()
