# pipelines/add_media_to_merged.py

from __future__ import annotations

import argparse
import logging
import re
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from stingray.images import resolve_frame_list_csv
from stingray.logging.setup import log_command_options, setup_logging

logger = logging.getLogger(__name__)


def _nearest_sensor_timestamp(
    media_times: np.ndarray,
    sensor_times: np.ndarray,
) -> np.ndarray:
    idx = np.searchsorted(sensor_times, media_times)

    idx = np.clip(idx, 1, len(sensor_times) - 1)

    left = sensor_times[idx - 1]
    right = sensor_times[idx]

    nearest_idx = np.where(
        np.abs(media_times - left) <= np.abs(media_times - right),
        idx - 1,
        idx,
    )

    return sensor_times[nearest_idx]


def add_media_to_merged(
    merged_csv: str | Path,
    cruise: str,
    media_list_dirs: list[str] | None = None,
    out_path: str | Path | None = None,
    overwrite: bool = False,
) -> Path:
    started_at = time.monotonic()
    if media_list_dirs is None:
        raise ValueError("Provide at least one frame-list CSV or directory.")

    merged_csv = Path(merged_csv)

    if out_path is None:
        out_path = merged_csv
    else:
        out_path = Path(out_path)

    logger.info(
        "Adding media to sensor data | sensor_csv=%s cruise=%s media_sources=%s "
        "out_path=%s overwrite=%s",
        merged_csv,
        cruise,
        media_list_dirs,
        out_path,
        overwrite,
    )
    sled = pd.read_csv(merged_csv)
    logger.info("Loaded %s sensor rows with %s columns.", len(sled), len(sled.columns))

    legacy_columns = [
        column for column in sled.columns
        if re.fullmatch(r"(?:media_path|id|link)(?:_[1-9][0-9]*)?", column)
    ]
    if legacy_columns:
        sled = sled.drop(columns=legacy_columns)

    for base_column in ("media", "frame"):
        alias = f"{base_column}_1"
        if alias not in sled.columns:
            continue
        if base_column in sled.columns:
            base_values = sled[base_column]
            alias_values = sled[alias]
            conflict = (
                base_values.notna()
                & alias_values.notna()
                & base_values.ne(alias_values)
            )
            if conflict.any():
                raise ValueError(
                    f"Conflicting first-stream columns: {base_column} and {alias}."
                )
            sled[base_column] = base_values.combine_first(alias_values)
            sled = sled.drop(columns=[alias])
        else:
            sled = sled.rename(columns={alias: base_column})

    if "timestamp" not in sled.columns:
        raise ValueError("Merged sensor file must contain 'timestamp' column.")

    sled = sled.sort_values("timestamp").copy()

    sensor_times = np.sort(
        sled["timestamp"].dropna().to_numpy(dtype=np.float64)
    )

    if len(sensor_times) < 2:
        raise ValueError("Merged sensor file must contain at least two timestamps.")

    resolved_sources = []
    for media_source in media_list_dirs:
        media_path = resolve_frame_list_csv(media_source, cruise)
        if media_path is None:
            raise FileNotFoundError(
                f"No frame-list CSV found for cruise {cruise} in {media_source}"
            )
        resolved_sources.append(media_path.resolve())

    logger.info("Resolved media streams: %s", resolved_sources)

    if len(set(resolved_sources)) != len(resolved_sources):
        raise ValueError("Each media stream must use a different frame-list CSV.")

    for media_i, media_path in enumerate(resolved_sources, start=1):

        tag = media_path.parent.name.lower()

        logger.info("Processing media: %s | %s", tag, media_path)

        media = pd.read_csv(
            media_path,
            usecols=lambda column: column in {"times", "media", "frame"},
        )
        input_rows = len(media)

        required_columns = {"times", "media", "frame"}
        missing_columns = sorted(required_columns.difference(media.columns))
        if missing_columns:
            raise ValueError(
                f"Frame-list CSV is missing {', '.join(missing_columns)}: {media_path}"
            )

        media["times"] = pd.to_datetime(media["times"], errors="coerce")

        media = (
            media
            .dropna(subset=["times"])
            .sort_values("times")
            .copy()
        )
        logger.info(
            "%s frame rows loaded; %s have valid timestamps.",
            input_rows,
            len(media),
        )

        if media.empty:
            raise ValueError(f"Frame-list CSV has no valid media times: {media_path}")

        origin = datetime(1904, 1, 1)

        media["timestamp"] = (
            media["times"] - origin
        ).dt.total_seconds()

        media = media[
            media["timestamp"].between(sensor_times[0], sensor_times[-1])
        ].copy()
        if media.empty:
            raise ValueError(
                f"Frame-list CSV does not overlap the sensor time range: {media_path}"
            )

        media["sensor_timestamp"] = _nearest_sensor_timestamp(
            media["timestamp"].to_numpy(dtype=np.float64),
            sensor_times,
        )

        media["time_distance"] = (
            media["timestamp"] - media["sensor_timestamp"]
        ).abs()
        nearest_rows = media.groupby("sensor_timestamp")["time_distance"].idxmin()
        media_agg = (
            media.loc[nearest_rows, ["sensor_timestamp", "media", "frame"]]
            .sort_values("sensor_timestamp")
            .reset_index(drop=True)
        )

        merge_key = "sensor_timestamp"

        if media_i > 1:
            suffix = f"_{media_i}"
            media_agg = media_agg.rename(
                columns={
                    c: f"{c}{suffix}"
                    for c in media_agg.columns
                    if c != merge_key
                }
            )

        media_cols = [
            c for c in media_agg.columns
            if c != merge_key
        ]

        existing_cols = [
            c for c in media_cols
            if c in sled.columns
        ]

        existing_with_data = [
            c for c in existing_cols
            if sled[c].notna().any()
        ]

        if existing_with_data and not overwrite:
            raise ValueError(
                f"Media columns already contain data for stream {media_i}: "
                f"{', '.join(existing_with_data)}. Use overwrite=True to replace them."
            )

        if existing_cols:
            logger.info(
                "Dropping existing media columns for %s: %s",
                tag,
                existing_cols,
            )
            sled = sled.drop(columns=existing_cols)

        sled = sled.merge(
            media_agg,
            left_on="timestamp",
            right_on=merge_key,
            how="left",
        )

        sled = sled.drop(columns=[merge_key], errors="ignore")

        logger.info(
            "%s media rows mapped to %s sensor timestamps.",
            tag,
            len(media_agg),
        )

    sled = sled.sort_values("timestamp")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    sled.to_csv(out_path, index=False)

    logger.info("Saved media-enriched file to: %s", out_path)
    logger.info(
        "Media enrichment complete | sensor_rows=%s streams=%s elapsed_seconds=%.2f",
        len(sled),
        len(resolved_sources),
        time.monotonic() - started_at,
    )

    return out_path


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(
        description="Attach media-list fields to an already merged Stingray sensor CSV."
    )

    parser.add_argument("merged_csv")
    parser.add_argument(
        "--work-dir",
        default=".",
        help="Workspace whose logs directory receives command logs.",
    )

    parser.add_argument("--cruise", required=True)

    parser.add_argument(
        "--media-list-dirs",
        nargs="+",
        default=None,
        help=(
            "Frame-list CSV files or directories containing one matching "
            "cruise frame-list CSV."
        ),
    )

    parser.add_argument(
        "--out-path",
        required=True,
        help="Output CSV path.",
    )

    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace existing populated media columns if present.",
    )
    parser.add_argument(
        "--no-file-log",
        action="store_true",
        help="Disable Stingray log files and write logs only to the console.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )

    args = parser.parse_args(argv)

    work_dir = Path(args.work_dir).expanduser().resolve()
    setup_logging(
        log_dir=work_dir / "logs",
        name=__name__,
        level=getattr(logging, args.log_level),
        file=not args.no_file_log,
    )
    log_command_options(logger, args)

    add_media_to_merged(
        merged_csv=args.merged_csv,
        cruise=args.cruise,
        media_list_dirs=args.media_list_dirs,
        out_path=args.out_path,
        overwrite=args.overwrite,
    )


if __name__ == "__main__":
    main()
