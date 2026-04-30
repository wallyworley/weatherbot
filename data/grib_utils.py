"""
GRIB2 byte-range utilities.

NOAA GRIB2 files on S3 ship with a companion `.idx` file that lists message
offsets and variable names. By parsing the idx first, we can issue a ranged
S3 GET to download only the messages we want — typically a few MB instead of
200-500 MB for a full NBM/HRRR file.

Usage:
    from weather_bot.data import grib_utils

    msgs = grib_utils.parse_idx(bucket, key)
    bytes_ = grib_utils.download_ranges(bucket, key, matching_messages)
    grib_utils.save_temp(bytes_, "/tmp/subset.grib2")
    ds = grib_utils.open_dataset("/tmp/subset.grib2")
"""
from __future__ import annotations

import io
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import boto3
from botocore import UNSIGNED
from botocore.client import Config
from tenacity import retry, stop_after_attempt, wait_exponential

log = logging.getLogger(__name__)

# NOAA Open Data buckets are public — no credentials needed.
_S3 = boto3.client("s3", config=Config(signature_version=UNSIGNED, retries={"max_attempts": 5}))


@dataclass(frozen=True)
class GribMessage:
    num: int
    start: int
    end: int | None   # None means "until EOF"
    line: str         # original idx line, useful for debugging

    @property
    def length(self) -> int | None:
        return None if self.end is None else self.end - self.start


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=10))
def _s3_get(bucket: str, key: str, range_header: str | None = None) -> bytes:
    kwargs = {"Bucket": bucket, "Key": key}
    if range_header:
        kwargs["Range"] = range_header
    resp = _S3.get_object(**kwargs)
    return resp["Body"].read()


def object_exists(bucket: str, key: str) -> bool:
    try:
        _S3.head_object(Bucket=bucket, Key=key)
        return True
    except Exception:
        return False


def parse_idx(bucket: str, key: str) -> list[GribMessage]:
    """Fetch and parse the `.idx` sidecar for a GRIB2 file.

    The idx is pipe-delimited: `num:start_byte:date:var:level:fcst:...`
    """
    idx_key = key + ".idx"
    raw = _s3_get(bucket, idx_key).decode("utf-8", errors="replace")
    lines = [l for l in raw.strip().split("\n") if l]
    messages: list[GribMessage] = []
    for i, line in enumerate(lines):
        parts = line.split(":")
        num = int(parts[0])
        start = int(parts[1])
        end: int | None
        if i + 1 < len(lines):
            end = int(lines[i + 1].split(":")[1])
        else:
            end = None
        messages.append(GribMessage(num=num, start=start, end=end, line=line))
    return messages


def filter_messages(messages: Iterable[GribMessage], selectors: Iterable[str]) -> list[GribMessage]:
    """Keep messages whose idx line contains any of the substring selectors.

    Example selector: ":TMP:2 m above ground:"
    """
    sel = list(selectors)
    out = []
    for m in messages:
        if any(s in m.line for s in sel):
            out.append(m)
    return out


def download_ranges(bucket: str, key: str, messages: Iterable[GribMessage]) -> bytes:
    """Download the selected message byte ranges and concatenate them.

    Concatenated GRIB2 messages are themselves a valid GRIB2 file — cfgrib
    will happily read them.
    """
    buf = io.BytesIO()
    for m in messages:
        if m.end is not None:
            rng = f"bytes={m.start}-{m.end - 1}"
        else:
            rng = f"bytes={m.start}-"
        chunk = _s3_get(bucket, key, range_header=rng)
        buf.write(chunk)
    return buf.getvalue()


def save_temp(data: bytes, suffix: str = ".grib2") -> Path:
    fd, path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
    except Exception:
        os.unlink(path)
        raise
    return Path(path)


def open_dataset(path: str | Path, backend_kwargs: dict | None = None):
    """Open a GRIB2 file as an xarray dataset via cfgrib."""
    import xarray as xr

    backend_kwargs = backend_kwargs or {"indexpath": ""}
    return xr.open_dataset(str(path), engine="cfgrib", backend_kwargs=backend_kwargs)


def nearest_point(ds, lat: float, lon: float):
    """Extract the value at the nearest grid point to (lat, lon).

    Handles both 1D rectilinear coordinates (typical of global/lat-lon
    grids like GFS) and 2D curvilinear coordinates (HRRR/NBM on Lambert
    Conformal, NAM on Rotated Pole, etc.).

    For 2D grids we minimize a cosine-weighted squared angular distance,
    which is monotonic with great-circle distance at small scales —
    good enough for argmin.
    """
    import numpy as np

    if "longitude" in ds.coords:
        lon_coord = "longitude"
        lat_coord = "latitude"
    elif "lon" in ds.coords:
        lon_coord = "lon"
        lat_coord = "lat"
    else:
        raise KeyError(f"No recognized lat/lon coords in dataset: {list(ds.coords)}")

    lat_arr = ds[lat_coord].values
    lon_arr = ds[lon_coord].values

    # Normalize target lon to match the dataset's convention.
    if float(np.nanmax(lon_arr)) > 180 and lon < 0:
        lon = lon + 360

    if lat_arr.ndim == 1 and lon_arr.ndim == 1:
        # Rectilinear grid — xarray handles this natively.
        return ds.sel({lat_coord: lat, lon_coord: lon}, method="nearest")

    # Curvilinear (2D) grid. Find the argmin by squared angular distance and
    # select with the underlying grid dims (usually 'y', 'x').
    lat_r = np.deg2rad(lat_arr)
    lon_r = np.deg2rad(lon_arr)
    t_lat_r = np.deg2rad(lat)
    t_lon_r = np.deg2rad(lon)
    dlat = lat_r - t_lat_r
    dlon = (lon_r - t_lon_r) * np.cos(t_lat_r)
    dist2 = dlat * dlat + dlon * dlon

    dims = ds[lat_coord].dims
    if len(dims) == 2:
        iy, ix = np.unravel_index(np.argmin(dist2), dist2.shape)
        return ds.isel({dims[0]: int(iy), dims[1]: int(ix)})
    if len(dims) == 1:
        return ds.isel({dims[0]: int(np.argmin(dist2))})
    raise ValueError(f"Unexpected lat/lon dim layout: {dims}")


def celsius_to_fahrenheit(x):
    return x * 9.0 / 5.0 + 32.0


def kelvin_to_fahrenheit(x):
    return (x - 273.15) * 9.0 / 5.0 + 32.0
