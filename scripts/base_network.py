# SPDX-License-Identifier: MIT
# -*- coding: utf-8 -*-

"""
Creates the network topology for South Africa from either South Africa"s shape file, GCCA map extract for 10 supply regions or 27-supply regions shape file as a PyPSA
network.


main
├── load_scenario_definition
├── get_years
├── load_buses_and_lines
│   └── if single bus: skip lines
│   └── else: load lines and create reverse direction links
├── build_base_network
│   ├── create_network
│   ├── set_snapshots
│   ├── set_investment_periods (if multi-year)
│   └── add_components_to_network
│       └── line_derating
├── pypsa.Network.export_to_netcdf


Relevant Settings
-----------------

.. code:: yaml

    snapshots:

    supply_regions:

    electricity:
        voltages:

    lines:
        types:
        s_max_pu:
        under_construction:

    links:
        p_max_pu:
        under_construction:
        include_tyndp:

    transformers:
        x:
        s_nom:
        type:

.. seealso::
    Documentation of the configuration file ``config.yaml`` at
    :ref:`snapshots_cf`, :ref:`toplevel_cf`, :ref:`electricity_cf`, :ref:`load_cf`,
    :ref:`lines_cf`, :ref:`links_cf`, :ref:`transformers_cf`

Inputs
------

- ``data/bundle/supply_regions/{regions}.shp``:  Shape file for different supply regions.
- ``data/bundle/South_Africa_100m_Population/ZAF15adjv4.tif``: Raster file of South African population from https://hub.worldpop.org/doi/10.5258/SOTON/WP00246
- ``data/num_lines.xlsx``: confer :ref:`lines`


Outputs
-------

- ``networks/base_{model_file}_{regions}.nc``

    .. image:: ../img/base.png
        :scale: 33 %
"""

import geopandas as gpd
import logging
import numpy as np
import pandas as pd
import pypsa
import os
import re
from _helpers import load_scenario_definition

def create_network():
    n = pypsa.Network()
    n.name = "PYPSA-RSA"
    return n

def load_buses_and_lines():
    buses = gpd.read_file(snakemake.input.buses)
    buses.set_index("name", drop=True,inplace=True)
    buses = buses[["x","y","v_nom","POP_2016", "GVA_2016",]]

    if len(buses) != 1:
        lines = gpd.read_file(snakemake.input.lines, index_col=[1])
        lines_reverse = lines.copy()
        lines_reverse.bus0 = lines.bus1.values
        lines_reverse.bus1 = lines.bus0.values

        lines.index = lines.bus0 + ['-'] + lines.bus1
        lines_reverse.index = lines_reverse.bus0 + ['-'] + lines_reverse.bus1

        lines = pd.concat([lines, lines_reverse])
    else:
        lines = []
    return buses, lines

def set_snapshots(n, years):
    def create_snapshots(year):
        snapshots = pd.date_range(start = f"{year}-01-01 00:00", end = f"{year}-12-31 23:00", freq="h")
        return snapshots[~((snapshots.month == 2) & (snapshots.day == 29))]  # exclude Feb 29 for leap years

    if not isinstance(years, int):
        snapshots = pd.DatetimeIndex([])
        for y in years:
            snapshots = snapshots.append(create_snapshots(y))
        n.set_snapshots(pd.MultiIndex.from_arrays([snapshots.year, snapshots]))
    else:
        n.set_snapshots(create_snapshots(years))

def set_investment_periods(n, years):
    discount_rate = SCENARIO_SETUP.loc["global_discount_rate"]
    n.investment_periods = years
    delta_years = list(np.diff(years))
    if len(years) > 1:
        n.investment_period_weightings["years"] = delta_years + [50]#[delta_years[-1]]
    else:
        n.investment_period_weightings["years"] = 1
    T = 0
    for period, nyears in n.investment_period_weightings.years.items():
        discounts = [(1 / (1 + discount_rate) ** t) for t in range(T, T + nyears)]
        n.investment_period_weightings.at[period, "objective"] = sum(discounts)
        T += nyears

def line_derating(n, lines):
    # convert each entry of n.investment_periods to str
    pu_max = pd.DataFrame(1, index = n.snapshots, columns=lines.index, dtype=float)
    for y in n.investment_periods:
        for l in pu_max.columns:
            pu_max.loc[y, l] = float(lines.loc[l, str(y)])

    return pu_max

def add_components_to_network(n, buses, lines, line_config):
    n.import_components_from_dataframe(buses, "Bus")
#   Only a transfer model is used, with allowable efficiency losses
#   This requires two uni-directional links between nodes as PyPSA efficiency is not bi-directional
    if len(buses) != 1:
        pu_max = line_derating(n, lines)
        n.madd(
            "Link",
            lines.index,
            p_nom = lines["St_Clair_limit_n1"],
            bus0 = lines["bus0"],
            bus1 = lines["bus1"],
            efficiency = 1-line_config["losses"]*lines["length"]/1000,
            p_max_pu = pu_max,
            p_min_pu = 0, # single direction 2 links represent 1 line
            lifetime = 100,
            build_year = n.investment_periods[0]-1,
            p_nom_extendable = False,
        )
        
def get_years():
    years = SCENARIO_SETUP.loc["simulation_years"]
    if not isinstance(years, int):
        if "range" in years:
            years = list(eval(years))
        elif "," not in years:
            years = [int(years)]
        else:
            years = list(map(int, re.split(r",\s*", years)))

    return years

def build_base_network(years, buses, lines, line_config):
    n = create_network()
    set_snapshots(n, years)
    if not isinstance(years, int):
        set_investment_periods(n, years)
    add_components_to_network(n, buses, lines, line_config)
    return n

if __name__ == "__main__":
    if "snakemake" not in globals():
        from _helpers import mock_snakemake
        snakemake = mock_snakemake(
            "base_network", 
            **{
                "scenario":"S1",
                "year":2045,
            }
        )
    SCENARIO_SETUP = load_scenario_definition(snakemake.wildcards.scenario, snakemake.config)

    line_config = snakemake.config["lines"]
    buses, lines = load_buses_and_lines()

    years = get_years()


    n = build_base_network(years, buses, lines, line_config)
    n.multi_invest = 1 if not isinstance(years, int) else 0
    n.export_to_netcdf(snakemake.output[0])



