from __future__ import annotations

import argparse
import logging
from pathlib import Path

from stingray.logging.setup import log_command_options, setup_logging
from stingray.sensors.merge import merge_sensors


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stingray CTD-binned sensor aggregation + media + casts"
    )

    parser.add_argument("--cruise", required=True)
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    parser.add_argument(
        "--work-dir",
        default=".",
        help="Workspace containing runtime inputs, outputs, indexes, and logs.",
    )
    parser.add_argument(
        "--root",
        default=None,
        help="Raw sensor-data root. Default: WORK_DIR/sensor_data.",
    )
    parser.add_argument("--cal-year", default="2021")
    parser.add_argument("--time-bin-seconds", type=float, default=5.0)
    parser.add_argument(
        "--out-dir",
        default=None,
        help="Output directory. Default: WORK_DIR/dash_data/data/stingray.",
    )
    parser.add_argument(
        "--index-dir",
        default=None,
        help="Sensor index directory. Default: WORK_DIR/indexes.",
    )
    parser.add_argument(
        "--media-list-dirs",
        nargs="*",
        default=None,
        help=(
            "Frame-list CSV files or directories containing one matching cruise "
            "frame-list CSV. "
            "Default: WORK_DIR/media_list/ISIIS1 and WORK_DIR/media_list/ISIIS2."
        ),
    )
    parser.add_argument(
        "--suna-cal-file",
        default=None,
        help="Optional SUNA calibration file.",
    )
    parser.add_argument(
        "--suna-cal-dir",
        default=None,
        help="Optional SUNA calibration directory, relative to WORK_DIR if needed.",
    )
    parser.add_argument("--overwrite-index", action="store_true")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )

    return parser.parse_args(argv)


def main(argv=None) -> None:
    args = parse_args(argv)

    # Resolve all default runtime paths from one movable workspace.
    work_dir = Path(args.work_dir).expanduser().resolve()
    root = Path(args.root).expanduser() if args.root else Path("sensor_data")
    out_dir = (
        Path(args.out_dir).expanduser()
        if args.out_dir
        else Path("dash_data") / "data" / "stingray"
    )
    index_dir = Path(args.index_dir).expanduser() if args.index_dir else Path("indexes")
    media_list_dirs = [
        Path(path).expanduser()
        for path in (
            args.media_list_dirs
            if args.media_list_dirs is not None
            else ["media_list/ISIIS1", "media_list/ISIIS2"]
        )
    ]
    suna_cal_file = Path(args.suna_cal_file).expanduser() if args.suna_cal_file else None
    suna_cal_dir = Path(args.suna_cal_dir).expanduser() if args.suna_cal_dir else None

    # Absolute paths remain unchanged; relative paths belong to the workspace.
    root = root if root.is_absolute() else work_dir / root
    out_dir = out_dir if out_dir.is_absolute() else work_dir / out_dir
    index_dir = index_dir if index_dir.is_absolute() else work_dir / index_dir
    media_list_dirs = [
        path if path.is_absolute() else work_dir / path
        for path in media_list_dirs
    ]
    if suna_cal_file is not None and not suna_cal_file.is_absolute():
        suna_cal_file = work_dir / suna_cal_file
    if suna_cal_dir is not None and not suna_cal_dir.is_absolute():
        suna_cal_dir = work_dir / suna_cal_dir

    logger = setup_logging(
        log_dir=work_dir / "logs",
        name=__name__,
        level=getattr(logging, args.log_level),
    )
    log_command_options(logger, args)

    merge_sensors(
        cruise=args.cruise,
        start=args.start,
        end=args.end,
        root=root,
        cal_year=args.cal_year,
        time_bin_seconds=args.time_bin_seconds,
        out_dir=out_dir,
        index_dir=index_dir,
        media_list_dirs=media_list_dirs,
        overwrite_index=args.overwrite_index,
        suna_cal_file=suna_cal_file,
        suna_cal_dir=suna_cal_dir,
    )


if __name__ == "__main__":
    main()
