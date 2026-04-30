"""Unit tests for grib_utils.nearest_point — especially 2D grid handling."""
import numpy as np
import xarray as xr

from weather_bot.data import grib_utils


def test_nearest_point_1d_grid():
    ds = xr.Dataset(
        {"tmp": (("latitude", "longitude"), np.arange(12).reshape(3, 4).astype(float))},
        coords={
            "latitude":  np.array([30.0, 35.0, 40.0]),
            "longitude": np.array([-100.0, -90.0, -80.0, -70.0]),
        },
    )
    pt = grib_utils.nearest_point(ds, 35.1, -89.9)
    assert float(pt["tmp"].values) == 5.0   # row=1, col=1


def test_nearest_point_2d_grid_like_hrrr():
    # Build a small 2D curvilinear grid — mimics HRRR's Lambert Conformal.
    ny, nx = 5, 7
    lats_1d = np.linspace(38.0, 42.0, ny)
    lons_1d = np.linspace(-76.0, -72.0, nx)
    LAT, LON = np.meshgrid(lats_1d, lons_1d, indexing="ij")
    values = (LAT * 10 + LON).astype(float)

    ds = xr.Dataset(
        {"tmp": (("y", "x"), values)},
        coords={
            "latitude":  (("y", "x"), LAT),
            "longitude": (("y", "x"), LON),
        },
    )

    # Target (40, -74) lies exactly on grid index (iy=2, ix=3).
    pt = grib_utils.nearest_point(ds, 40.0, -74.0)
    expected = LAT[2, 3] * 10 + LON[2, 3]
    assert np.isclose(float(pt["tmp"].values), expected, atol=0.01)


def test_nearest_point_2d_grid_lon_wrap():
    # Dataset stored in 0..360 convention; query with negative lon.
    ny, nx = 3, 4
    LAT, LON = np.meshgrid(
        np.linspace(38.0, 42.0, ny),
        np.linspace(280.0, 290.0, nx),   # -80 .. -70 in 0..360
        indexing="ij",
    )
    ds = xr.Dataset(
        {"tmp": (("y", "x"), (LAT + LON).astype(float))},
        coords={
            "latitude":  (("y", "x"), LAT),
            "longitude": (("y", "x"), LON),
        },
    )
    pt = grib_utils.nearest_point(ds, 40.0, -74.0)   # should remap to 286
    # Closest lon is the one nearest to 286 in LON[0, :]
    target_col = int(np.argmin(np.abs(LON[0, :] - 286.0)))
    target_row = 1  # middle of three lat rows
    expected = LAT[target_row, target_col] + LON[target_row, target_col]
    assert np.isclose(float(pt["tmp"].values), expected, atol=0.01)
