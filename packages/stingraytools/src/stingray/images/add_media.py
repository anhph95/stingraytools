# pipelines/add_media_to_merged.py

from __future__ import annotations

import argparse
import logging
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
    if media_list_dirs is None:
        media_list_dirs = ["media_list/ISIIS1", "media_list/ISIIS2"]

    merged_csv = Path(merged_csv)

    if out_path is None:
        out_path = merged_csv
    else:
        out_path = Path(out_path)

    sled = pd.read_csv(merged_csv)

    if "timestamp" not in sled.columns:
        raise ValueError("Merged sensor file must contain 'timestamp' column.")

    sled = sled.sort_values("timestamp").copy()

    sensor_times = sled["timestamp"].to_numpy(dtype=np.float64)

    if len(sensor_times) < 2:
        raise ValueError("Merged sensor file must contain at least two timestamps.")

    for media_i, media_source in enumerate(media_list_dirs, start=1):
        media_path = resolve_frame_list_csv(media_source, cruise)
        if media_path is None:
            logger.warning(
                "No frame-list CSV found for cruise %s in %s",
                cruise,
                media_source,
            )
            continue

        tag = media_path.parent.name.lower()

        logger.info("Processing media: %s | %s", tag, media_path)

        media = pd.read_csv(media_path)

        if "times" not in media.columns:
            raise ValueError(f"Frame-list CSV must contain a 'times' column: {media_path}")

        media["times"] = pd.to_datetime(media["times"], errors="coerce")

        media = (
            media
            .dropna(subset=["times"])
            .sort_values("times")
            .copy()
        )

        if media.empty:
            logger.warning("Skipping %s because it has no valid media times.", media_path)
            continue

        origin = datetime(1904, 1, 1)

        media["timestamp"] = (
            media["times"] - origin
        ).dt.total_seconds()

        media["sensor_timestamp"] = _nearest_sensor_timestamp(
            media["timestamp"].to_numpy(dtype=np.float64),
            sensor_times,
        )

        media_agg = (
            media
            .sort_values("timestamp")
            .groupby("sensor_timestamp", as_index=False)
            .agg(
                {
                    c: "first"
                    for c in media.columns
                    if c not in [
                        "times",
                        "timestamp",
                        "sensor_timestamp",
                    ]
                }
            )
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
            logger.info(
                "Skipping %s because media columns already contain data: %s",
                tag,
                existing_with_data,
            )
            continue

        if existing_cols and overwrite:
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

    args = parser.parse_args(argv)

    work_dir = Path(args.work_dir).expanduser().resolve()
    setup_logging(
        log_dir=work_dir / "logs",
        name="stingray_images_add_media",
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
