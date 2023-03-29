#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# SPDX-FileCopyrightText: : 2017-2022 The PyPSA-Eur Authors
#
# SPDX-License-Identifier: MIT

"""
Calculates for each network node the (i) installable capacity (based on land-
use), (ii) the available generation time series (based on weather data), and
(iii) the average distance from the node for onshore wind, AC-connected
offshore wind, DC-connected offshore wind and solar PV generators. In addition
for offshore wind it calculates the fraction of the grid connection which is
under water.
.. note:: Hydroelectric profiles are built in script :mod:`build_hydro_profiles`.
Relevant settings
-----------------
.. code:: yaml
    snapshots:
    atlite:
        nprocesses:
    renewable:
        {technology}:
            cutout:
            corine:
            grid_codes:
            distance:
            natura:
            max_depth:
            max_shore_distance:
            min_shore_distance:
            capacity_per_sqkm:
            correction_factor:
            potential:
            min_p_max_pu:
            clip_p_max_pu:
            resource:
.. seealso::
    Documentation of the configuration file ``config.yaml`` at
    :ref:`snapshots_cf`, :ref:`atlite_cf`, :ref:`renewable_cf`
Inputs
------
- ``data/bundle/corine/g250_clc06_V18_5.tif``: `CORINE Land Cover (CLC) <https://land.copernicus.eu/pan-european/corine-land-cover>`_ inventory on `44 classes <https://wiki.openstreetmap.org/wiki/Corine_Land_Cover#Tagging>`_ of land use (e.g. forests, arable land, industrial, urban areas).
    .. image:: ../img/corine.png
        :scale: 33 %
- ``data/bundle/GEBCO_2014_2D.nc``: A `bathymetric <https://en.wikipedia.org/wiki/Bathymetry>`_ data set with a global terrain model for ocean and land at 15 arc-second intervals by the `General Bathymetric Chart of the Oceans (GEBCO) <https://www.gebco.net/data_and_products/gridded_bathymetry_data/>`_.
    .. image:: ../img/gebco_2019_grid_image.jpg
        :scale: 50 %
    **Source:** `GEBCO <https://www.gebco.net/data_and_products/images/gebco_2019_grid_image.jpg>`_
- ``resources/natura.tiff``: confer :ref:`natura`
- ``resources/offshore_shapes.geojson``: confer :ref:`shapes`
- ``resources/regions_onshore.geojson``: (if not offshore wind), confer :ref:`busregions`
- ``resources/regions_offshore.geojson``: (if offshore wind), :ref:`busregions`
- ``"cutouts/" + config["renewable"][{technology}]['cutout']``: :ref:`cutout`
- ``networks/base.nc``: :ref:`base`
Outputs
-------
- ``resources/profile_{technology}.nc`` with the following structure
    ===================  ==========  =========================================================
    Field                Dimensions  Description
    ===================  ==========  =========================================================
    profile              bus, time   the per unit hourly availability factors for each node
    -------------------  ----------  ---------------------------------------------------------
    weight               bus         sum of the layout weighting for each node
    -------------------  ----------  ---------------------------------------------------------
    p_nom_max            bus         maximal installable capacity at the node (in MW)
    -------------------  ----------  ---------------------------------------------------------
    potential            y, x        layout of generator units at cutout grid cells inside the
                                     Voronoi cell (maximal installable capacity at each grid
                                     cell multiplied by capacity factor)
    -------------------  ----------  ---------------------------------------------------------
    average_distance     bus         average distance of units in the Voronoi cell to the
                                     grid node (in km)
    -------------------  ----------  ---------------------------------------------------------
    underwater_fraction  bus         fraction of the average connection distance which is
                                     under water (only for offshore)
    ===================  ==========  =========================================================
    - **profile**
    .. image:: ../img/profile_ts.png
        :scale: 33 %
        :align: center
    - **p_nom_max**
    .. image:: ../img/p_nom_max_hist.png
        :scale: 33 %
        :align: center
    - **potential**
    .. image:: ../img/potential_heatmap.png
        :scale: 33 %
        :align: center
    - **average_distance**
    .. image:: ../img/distance_hist.png
        :scale: 33 %
        :align: center
    - **underwater_fraction**
    .. image:: ../img/underwater_hist.png
        :scale: 33 %
        :align: center
Description
-----------
This script functions at two main spatial resolutions: the resolution of the
network nodes and their `Voronoi cells
<https://en.wikipedia.org/wiki/Voronoi_diagram>`_, and the resolution of the
cutout grid cells for the weather data. Typically the weather data grid is
finer than the network nodes, so we have to work out the distribution of
generators across the grid cells within each Voronoi cell. This is done by
taking account of a combination of the available land at each grid cell and the
capacity factor there.
First the script computes how much of the technology can be installed at each
cutout grid cell and each node using the `GLAES
<https://github.com/FZJ-IEK3-VSA/glaes>`_ library. This uses the CORINE land use data,
Natura2000 nature reserves and GEBCO bathymetry data.
.. image:: ../img/eligibility.png
    :scale: 50 %
    :align: center
To compute the layout of generators in each node's Voronoi cell, the
installable potential in each grid cell is multiplied with the capacity factor
at each grid cell. This is done since we assume more generators are installed
at cells with a higher capacity factor.
.. image:: ../img/offwinddc-gridcell.png
    :scale: 50 %
    :align: center
.. image:: ../img/offwindac-gridcell.png
    :scale: 50 %
    :align: center
.. image:: ../img/onwind-gridcell.png
    :scale: 50 %
    :align: center
.. image:: ../img/solar-gridcell.png
    :scale: 50 %
    :align: center
This layout is then used to compute the generation availability time series
from the weather data cutout from ``atlite``.
Two methods are available to compute the maximal installable potential for the
node (`p_nom_max`): ``simple`` and ``conservative``:
- ``simple`` adds up the installable potentials of the individual grid cells.
  If the model comes close to this limit, then the time series may slightly
  overestimate production since it is assumed the geographical distribution is
  proportional to capacity factor.
- ``conservative`` assertains the nodal limit by increasing capacities
  proportional to the layout until the limit of an individual grid cell is
  reached.
"""
import functools
import logging
import time
import pypsa
import atlite
import pandas as pd
import geopandas as gpd
import numpy as np
import progressbar as pgb
import xarray as xr
import rioxarray
import rasterio
from _helpers import configure_logging
from dask.distributed import Client, LocalCluster
from pypsa.geo import haversine
from shapely.geometry import LineString

logger = logging.getLogger(__name__)

SALANDCOVER_CRS = "EPSG:32635"

def remove_leap_day(df):
    return df[~((df.index.month == 2) & (df.index.day == 29))]

if __name__ == "__main__":
    if "snakemake" not in globals():
        from _helpers import mock_snakemake

        #snakemake = mock_snakemake("build_renewable_profiles", technology="onwind")
        snakemake = mock_snakemake('build_renewable_profiles',technology='solar', 
                    **{'regions':'1-supply',
                    'resarea':'redz'})
    configure_logging(snakemake)
    pgb.streams.wrap_stderr()

    nprocesses = int(snakemake.threads)
    noprogress = not snakemake.config["atlite"].get("show_progress", False)
    config = snakemake.config["renewable"][snakemake.wildcards.technology]
    resource = config["resource"]  # pv panel config / wind turbine config
    correction_factor = config.get("correction_factor", 1.0)
    capacity_per_sqkm = config["capacity_per_sqkm"]
    p_nom_max_meth = config.get("potential", "conservative")
  
    if correction_factor != 1.0:
        logger.info(f"correction_factor is set as {correction_factor}")

    cluster = LocalCluster(n_workers=nprocesses, threads_per_worker=1)
    client = Client(cluster, asynchronous=True)

    cutout = atlite.Cutout(snakemake.input["cutout"])
    regions = gpd.read_file(snakemake.input.regions).to_crs(snakemake.config["crs"]["geo_crs"])
    assert not regions.empty, (
        f"List of regions in {snakemake.input.regions} is empty, please "
        "disable the corresponding renewable technology"
    )

    if ((snakemake.wildcards.technology=='onwind') & 
        (snakemake.config["atlite"]["apply_wind_correction"])):
        gwa_data = rioxarray.open_rasterio(snakemake.input.gwa_map)
        ds=gwa_data.sel(band=1, x=slice(*cutout.extent[[0,1]]), y=slice(*cutout.extent[[3,2]]))
        ds=ds.where(ds!=-999)
        ds=atlite.gis.regrid(ds,cutout.data.x, cutout.data.y,resampling=rasterio.warp.Resampling.average)
        cutout.data.wnd100m.mean("time").plot()

        bias_correction=ds/cutout.data.wnd100m.mean("time")
        bias_correction=bias_correction.fillna(1)
        cutout.data.wnd100m.values=(bias_correction*cutout.data.wnd100m).transpose('time', 'y', 'x').values
          
    # do not pull up, set_index does not work if geo dataframe is empty
    regions = regions.set_index("name").rename_axis("bus")
    buses = regions.index
    area_crs = snakemake.config["crs"]["area_crs"]
    resarea = gpd.read_file(snakemake.input.resarea).to_crs(area_crs)

    res = config.get("excluder_resolution", 100)
    excluder = atlite.ExclusionContainer(crs=3035, res=res)

    # limit to resareas REDZ or CORRIDORS
    excluder.add_geometry(resarea.geometry,invert=True)

    # exclude protected and conservation areas
    if config["natura"]:
        excluder.add_raster(snakemake.input.natura, nodata=0, allow_no_overlap=True)

    # exclude landuse types given by the grid codes
    if "salandcover" in config and config["salandcover"]:
        salandcover = config["salandcover"]
        excluder.add_raster(
            snakemake.input.salandcover,
            codes=salandcover["grid_codes"],
            invert=True,
            crs=SALANDCOVER_CRS,
        )
        # add a distance buffer to the landuse gridcode
        if "distance" in salandcover and config["salandcover"]["distance"] > 0:
            excluder.add_raster(
                snakemake.input.salandcover,
                codes=salandcover["distance_grid_codes"],
                buffer=salandcover["distance"],
                crs=SALANDCOVER_CRS,
            )

    #kwargs = dict(nprocesses=nprocesses, disable_progressbar=noprogress)
    if noprogress:
        logger.info("Calculate landuse availabilities...")
        start = time.time()
        availability = cutout.availabilitymatrix(regions, excluder)#, **kwargs) #TODO fix to run in parallel
        duration = time.time() - start
        logger.info(f"Completed availability calculation ({duration:2.2f}s)")
    else:
        availability = cutout.availabilitymatrix(regions, excluder)#, **kwargs)

    area = cutout.grid.to_crs(3035).area / 1e6
    area = xr.DataArray(
        area.values.reshape(cutout.shape), [cutout.coords["y"], cutout.coords["x"]]
    )

    potential = capacity_per_sqkm * availability.sum("bus") * area
    func = getattr(cutout, resource.pop("method"))
    resource["dask_kwargs"] = {"scheduler": client}
    capacity_factor = correction_factor * func(capacity_factor=True, **resource)
    layout = capacity_factor * area * capacity_per_sqkm
    profile, capacities = func(
        matrix=availability.stack(spatial=["y", "x"]),
        layout=layout,
        index=buses,
        per_unit=True,
        return_capacity=True,
        **resource,
    )

    logger.info(f"Calculating maximal capacity per bus (method '{p_nom_max_meth}')")
    if p_nom_max_meth == "simple":
        p_nom_max = capacity_per_sqkm * availability @ area
    elif p_nom_max_meth == "conservative":
        max_cap_factor = capacity_factor.where(availability != 0).max(["x", "y"])
        p_nom_max = capacities / max_cap_factor
    else:
        raise AssertionError(
            'Config key `potential` should be one of "simple" '
            f'(default) or "conservative", not "{p_nom_max_meth}"'
        )

    logger.info("Calculate average distances.")
    layoutmatrix = (layout * availability).stack(spatial=["y", "x"])

    coords = cutout.grid[["x", "y"]]
    bus_coords = regions[["x", "y"]]

    average_distance = []
    centre_of_mass = []
    for bus in buses:
        row = layoutmatrix.sel(bus=bus).data
        nz_b = row != 0
        row = row[nz_b]
        co = coords[nz_b]
        distances = haversine(bus_coords.loc[bus], co)
        average_distance.append((distances * (row / row.sum())).sum())
        centre_of_mass.append(co.values.T @ (row / row.sum()))

    average_distance = xr.DataArray(average_distance, [buses])
    centre_of_mass = xr.DataArray(centre_of_mass, [buses, ("spatial", ["x", "y"])])

    ds = xr.merge(
        [
            (correction_factor * profile).rename("profile"),
            capacities.rename("weight"),
            p_nom_max.rename("p_nom_max"),
            potential.rename("potential"),
            average_distance.rename("average_distance"),
        ]
    )
    ds.sel(time=~((ds.time.dt.month == 2) & (ds.time.dt.day == 29)))

    ds.to_netcdf(snakemake.output.profile)