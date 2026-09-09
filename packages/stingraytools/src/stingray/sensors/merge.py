# pipelines/process_cruise.py

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from stingray.config.columns import SLED_COLUMNS
from stingray.images import resolve_frame_list_csv
from stingray.io.csv import read_csv_parallel
from stingray.io.indexing import load_or_build_file_index, filter_file_index
from stingray.utils.temporal import convert_timestamp
from stingray.utils.gridding import assign_time_bins
from stingray.utils.spatial import convert_gps
from stingray.sensors.ctd import density_ies80
from stingray.sensors.fluorometer import calibrate_chlorophyll, calibrate_backscatter
from stingray.sensors.par import calibrate_par
from stingray.sensors.suna import calibrate_nitrate
from stingray.profiles.identify import identify_profiles

logger = logging.getLogger(__name__)

def merge_sensors(
    cruise: str,
    start: str,
    end: str,
    root: str | Path = "sensor_data",
    cal_year: str = "2021",
    time_bin_seconds: float = 5.0,
    out_dir: str | Path = "dash_data/data/stingray/",
    index_dir: str | Path = "indexes",
    media_list_dirs: list[str | Path] | None = None,
    overwrite_index: bool = False,
    suna_cal_file: str | Path | None = None,
    suna_cal_dir: str | Path | None = None,
) -> Path | None:
    # Logging the function entry and parameters for better traceability.
    logger.info(
        "Merging sensors data | cruise=%s start=%s end=%s root=%s cal_year=%s "
        "time_bin_seconds=%s out_dir=%s index_dir=%s overwrite_index=%s "
        "suna_cal_file=%s suna_cal_dir=%s media_dirs=%s",
        cruise,
        start,
        end,
        root,
        cal_year,
        time_bin_seconds,
        out_dir,
        index_dir,
        overwrite_index,
        suna_cal_file,
        suna_cal_dir,
        media_list_dirs,
    )
    if media_list_dirs is None:
        media_list_dirs = ["media_list/ISIIS1", "media_list/ISIIS2"]

    start_date = datetime.strptime(start, "%Y-%m-%d")
    end_date = datetime.strptime(end, "%Y-%m-%d")

    root = Path(root)
    out_dir = Path(out_dir)
    index_dir = Path(index_dir)

    index_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    # -------------------------
    # LOAD RAW SENSORS
    # -------------------------
    logger.info("Scanning raw sensor files...")
    sensor_names = ["CTD", "DVL", "Fluorometer", "GPS", "Oxygen", "PAR", "SUNA"]
    sensors = {}
       
    for name in sensor_names:
        idx = load_or_build_file_index(
            root / name,
            index_dir / f"{name}_index.csv",
            overwrite=overwrite_index,
        )

        files = filter_file_index(idx, start_date, end_date)
        sensors[name.lower()] = read_csv_parallel(files)

        logger.info(
            "%s: %s files, %s rows",
            name,
            len(files),
            len(sensors[name.lower()]),
        )

    # -------------------------
    # DEFINE TIME GRID FROM CTD
    # -------------------------
    logger.info("Gridding all sensors to %s-second bins...", time_bin_seconds)
    ctd = sensors["ctd"].copy()

    if ctd.empty:
        logger.error("[%s] CTD missing — skipping cruise.", cruise)
        return None

    t_ctd = ctd["Timestamp"].to_numpy(float)
    grid_start = np.floor(np.nanmin(t_ctd) / time_bin_seconds) * time_bin_seconds
    grid_end = np.ceil(np.nanmax(t_ctd) / time_bin_seconds) * time_bin_seconds

    # -------------------------
    # ATTACH time_bin TO ALL SENSORS
    # -------------------------
    for key, df in sensors.items():
        if df.empty:
            df["time_bin"] = np.nan
            continue

        sensors[key]["time_bin"] = assign_time_bins(
            np.asarray(df["Timestamp"], dtype=np.float64),
            time_bin_seconds,
            grid_start,
            grid_end,
        )

    # -------------------------
    # CTD BLOCK
    # -------------------------
    ctd = sensors["ctd"]

    ctd["density"] = density_ies80(
        ctd["Salinity"].to_numpy(float),
        ctd["Temperature"].to_numpy(float),
        (ctd["Pressure"] / 10.0).to_numpy(float),
    )

    ctd_agg = (
        ctd.sort_values("Timestamp")
        .groupby("time_bin", as_index=False)
        .agg(
            {
                "Temperature": "median",
                "Conductivity": "median",
                "Pressure": "median",
                "Salinity": "median",
                "Sound Velocity": "median",
                "density": "median",
                "Depth": "first",
            }
        )
    )

    logger.info("CTD bins: %s", len(ctd_agg))

    # -------------------------
    # GPS BLOCK
    # -------------------------
    gps = sensors["gps"]

    if not gps.empty:
        gps["Latitude"] = convert_gps(gps["Latitude"], gps["Latitude Hemisphere"])
        gps["Longitude"] = convert_gps(gps["Longitude"], gps["Longitude Hemisphere"])

    gps_agg = (
        gps.sort_values("Timestamp")
        .groupby("time_bin", as_index=False)
        .agg({"Latitude": "first", "Longitude": "first"})
    )

    gps_agg = gps_agg.set_index("time_bin").reindex(ctd_agg["time_bin"]).reset_index()

    logger.info(
        "GPS bins raw: %s / %s",
        gps_agg[["Latitude", "Longitude"]].notna().all(axis=1).sum(),
        len(gps_agg),
    )

    # -------------------------
    # DVL BLOCK
    # -------------------------
    dvl = sensors["dvl"]
    dvl_agg_columns = {
        "anglePitch": "median",
        "angleRoll": "median",
        "angleHeading": "median",
        "distanceAltitude": "median",
        "velocityWaterTransverse": "median",
        "velocityWaterLongitudinal": "median",
        "velocityWaterNormal": "median",
        "velocityGroundTransverse": "median",
        "velocityGroundLongitudinal": "median",
        "velocityGroundVertical": "median",
    }
    dvl_agg_columns = {
        name: method
        for name, method in dvl_agg_columns.items()
        if name in dvl.columns
    }

    dvl_agg = (
        dvl.sort_values("Timestamp")
        .groupby("time_bin", as_index=False)
        .agg(dvl_agg_columns)
    )

    logger.info("DVL bins: %s", len(dvl_agg))

    # -------------------------
    # FLUOROMETER BLOCK
    # -------------------------
    fluoro = sensors["fluorometer"]

    if not fluoro.empty:
        fluoro["Chlorophyll"] = calibrate_chlorophyll(
            fluoro["Chlorophyll"],
            year=cal_year,
        )
        fluoro["Backscattering"] = calibrate_backscatter(
            fluoro["Backscattering"],
            year=cal_year,
        )

    fluoro_agg = (
        fluoro.sort_values("Timestamp")
        .groupby("time_bin", as_index=False)
        .agg({"Chlorophyll": "median", "Backscattering": "median"})
    )

    logger.info("Fluorometer bins: %s", len(fluoro_agg))

    # -------------------------
    # OXYGEN BLOCK
    # -------------------------
    oxygen = sensors["oxygen"]

    oxygen_agg = (
        oxygen.sort_values("Timestamp")
        .groupby("time_bin", as_index=False)
        .agg({"O2Concentration": "median", "AirSaturation": "median"})
    )

    logger.info("Oxygen bins: %s", len(oxygen_agg))

    # -------------------------
    # PAR BLOCK
    # -------------------------
    par = sensors["par"]

    if not par.empty:
        par["Raw PAR [V]"] = calibrate_par(
            par["Raw PAR [V]"],
            year=cal_year,
        )

    par_agg = (
        par.sort_values("Timestamp")
        .groupby("time_bin", as_index=False)
        .agg({"Raw PAR [V]": "median"})
    )

    logger.info("PAR bins: %s", len(par_agg))

    # -------------------------
    # SUNA BLOCK
    # -------------------------
    suna = sensors["suna"]

    if not suna.empty:
        if suna_cal_file is None and suna_cal_dir is None:
            # Fast path: no TSP correction.
            suna["NitrateConcentration[uM]"] = calibrate_nitrate(
                suna,
                cruise,
                cal_file=None,
                cal_dir=None,
            )

        else:
            # Slow path: TSP correction needs CTD fields and matdate.
            suna["Times"], suna["matdate"] = convert_timestamp(suna["Timestamp"])

            ctd_sorted = ctd.sort_values("Timestamp")
            t_ctd = ctd_sorted["Timestamp"].to_numpy(dtype=np.float64)
            t_suna = suna["Timestamp"].to_numpy(dtype=np.float64)

            suna["Salinity"] = np.interp(
                t_suna,
                t_ctd,
                ctd_sorted["Salinity"].to_numpy(dtype=np.float64),
                left=np.nan,
                right=np.nan,
            )

            suna["Temperature"] = np.interp(
                t_suna,
                t_ctd,
                ctd_sorted["Temperature"].to_numpy(dtype=np.float64),
                left=np.nan,
                right=np.nan,
            )

            suna["Pressure"] = np.interp(
                t_suna,
                t_ctd,
                ctd_sorted["Pressure"].to_numpy(dtype=np.float64),
                left=np.nan,
                right=np.nan,
            )

            suna["NitrateConcentration[uM]"] = calibrate_nitrate(
                suna,
                cruise,
                cal_file=suna_cal_file,
                cal_dir=suna_cal_dir,
            )

    suna_agg = (
        suna.sort_values("Timestamp")
        .groupby("time_bin", as_index=False)
        .agg({"NitrateConcentration[uM]": "median"})
    )

    logger.info("SUNA bins: %s", len(suna_agg))

    # -------------------------
    # CAST / PROFILE SEGMENTATION
    # -------------------------
    logger.info("Identifying deployments and casts from CTD...")
    cast, deployment = identify_profiles(
        depth=ctd_agg["Depth"].to_numpy(np.float64),
        time_seconds=ctd_agg["time_bin"].to_numpy(np.float64),
        bin_size=1.0,
        dir_window=4,
        max_gap_sec=1200.0, # 20 minutes
        min_turn_depth=5.0,
    )

    ctd_agg["cast"] = pd.Series(cast).astype("Int64")
    ctd_agg["deployment"] = pd.Series(deployment).astype("Int64")

    # -------------------------
    # MEDIA BLOCK
    # -------------------------
    media_aggs = []

    for media_source in media_list_dirs:
        media_path = resolve_frame_list_csv(media_source, cruise)
        if media_path is None:
            logger.warning(
                "No frame-list CSV found for cruise %s in %s",
                cruise,
                media_source,
            )
            continue

        tag = media_path.parent.name.lower()
        logger.info("Processing media: %s", tag)

        media = pd.read_csv(media_path)
        if "times" not in media.columns:
            raise ValueError(f"Frame-list CSV must contain a 'times' column: {media_path}")
        media["times"] = pd.to_datetime(media["times"], errors="coerce")
        media = media.dropna(subset=["times"]).sort_values("times")

        origin = datetime(1904, 1, 1)
        media["timestamp"] = (media["times"] - origin).dt.total_seconds()

        media["time_bin"] = assign_time_bins(
            np.asarray(media["timestamp"], dtype=np.float64),
            time_bin_seconds,
            grid_start,
            grid_end,
        )

        media_agg = (
            media.sort_values("timestamp")
            .groupby("time_bin", as_index=False)
            .agg(
                {
                    c: "first"
                    for c in media.columns
                    if c not in ["time_bin", "times", "timestamp"]
                }
            )
        )

        media_aggs.append(media_agg)
        logger.info("%s bins: %s", tag, len(media_agg))

    if not media_aggs:
        logger.warning("No media files found...")
        
    # -------------------------
    # MERGE MASTER TABLE
    # -------------------------
    sled = (
        ctd_agg
        .merge(gps_agg, on="time_bin", how="left")
        .merge(dvl_agg, on="time_bin", how="left")
        .merge(fluoro_agg, on="time_bin", how="left")
        .merge(par_agg, on="time_bin", how="left")
        .merge(oxygen_agg, on="time_bin", how="left")
        .merge(suna_agg, on="time_bin", how="left")
    )

    for i, media_agg in enumerate(media_aggs, start=1):
        if i == 1:
            sled = sled.merge(media_agg, on="time_bin", how="left")
        else:
            suffix = f"_{i}"
            cols = [c for c in media_agg.columns if c != "time_bin"]
            sled = sled.merge(
                media_agg.rename(columns={c: f"{c}{suffix}" for c in cols}),
                on="time_bin",
                how="left",
            )

    # -------------------------
    # POST-MERGE PROCESSING
    # -------------------------
    sled["timestamp"] = sled["time_bin"]
    sled["times"] = pd.to_datetime(convert_timestamp(sled["time_bin"])[0])
    sled = sled.drop(columns=["time_bin"], errors="ignore")
    sled = sled.sort_values("timestamp")

    sled.rename(columns=SLED_COLUMNS, inplace=True)

    # -------------------------
    # QC: GPS INTERPOLATION WITHIN DEPLOYMENTS
    # -------------------------
    gps_cols = ["latitude", "longitude"]

    if all(c in sled.columns for c in gps_cols) and "deployment" in sled.columns:
        tmp = sled.set_index("times")[gps_cols].copy()
        dep = sled.set_index("times")["deployment"]

        for dep_id in dep.dropna().unique():
            mask = dep == dep_id
            tmp.loc[mask, gps_cols] = (
                tmp.loc[mask, gps_cols]
                .interpolate(method="time", limit_area="inside")
            )

        sled = sled.set_index("times")
        sled.loc[tmp.index, gps_cols] = tmp[gps_cols]
        sled = sled.reset_index()

        logger.info("QC: %s GPS points interpolated within deployments.", tmp[gps_cols].isnull().sum().sum())

    # -------------------------
    # SAVE OUTPUT
    # -------------------------
    out_path = out_dir / f"{start_date.strftime('%Y%m%d')}_{cruise}.csv"
    sled.to_csv(out_path, index=False)
    
    logger.info("Merge complete for cruise %s | %s to %s", cruise, start, end)
    logger.info("Bin size: %s seconds", time_bin_seconds)
    logger.info("Data points: %s", len(sled))
    logger.info("Number of deployments: %s", ctd_agg["deployment"].nunique())
    logger.info("Number of casts: %s", ctd_agg["cast"].nunique())
    logger.info("Output saved to: %s", out_path)

    return out_path
