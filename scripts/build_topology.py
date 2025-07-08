# SPDX-FileCopyrightText:  PyPSA-ZA2, PyPSA-ZA Authors
# SPDX-License-Identifier: MIT
# coding: utf-8

"""
Creates the network topology (buses and lines).

Relevant Settings
-----------------

.. code:: yaml

    snapshots:

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

.. seealso::
    Documentation of the configuration file ``config.yaml`` at
    :ref:`snapshots_cf`, :ref:`toplevel_cf`, :ref:`electricity_cf`, :ref:`load_cf`,
    :ref:`lines_cf`, :ref:`links_cf`, :ref:`transformers_cf`

Inputs
------

- ``data/bundle/supply_regions/{regions}.shp``:  Shape file for different supply regions.
- ``data/num_lines.xlsx``: confer :ref:`links`

Outputs
-------
- ``resources/buses_{regions}.geojson``
- ``resources/lines_{regions}.geojson``

"""

import networkx as nx
import pandas as pd
import geopandas as gpd
from shapely.geometry import LineString, Point
import logging
import numpy as np
from operator import attrgetter
import os
import pypsa
import re
from _helpers import save_to_geojson, load_scenario_definition
from base_network import get_years
from pypsa.geo import haversine


def load_region_data(model_regions):
    # Load supply regions and calculate population per region
    regions = gpd.read_file(
        snakemake.input.supply_regions,
        layer=model_regions,
    ).to_crs(snakemake.config["gis"]["crs"]["distance_crs"])

    possible_index_cols = ['name', 'Name', 'LocalArea', 'SupplyArea']

    index_column = [col for col in possible_index_cols if col in regions.columns]
    regions = regions.set_index(index_column[0])
    regions.index.name = 'name'

    gdp_pop = gpd.read_file(
        snakemake.input.gdp_pop_data,
    ).to_crs(snakemake.config["gis"]["crs"]["distance_crs"])

    joined = gpd.sjoin(gdp_pop, regions, how="inner", predicate="within")
    gva_cols = ["SIC1_2016", "SIC2_2016", "SIC3_2016", "SIC4_2016", "SIC6_2016", "SIC7_2016", "SIC8_2016", "SIC9_2016"]
    pop_col = ["POP_2016"]
    for col in gva_cols + pop_col:
        try:
            regions[col] = joined[col].groupby(joined.name).sum()
        except:
            regions[col] = joined[col].groupby(joined.index_right).sum()
    
    regions["GVA_2016"] = regions[gva_cols].sum(axis=1)
    if len(regions)>1:
        regions.drop(["Shape_Area", "Shape_Leng"], axis=1, inplace=True)

    return regions

def load_line_data(line_config):

    lines = gpd.read_file(snakemake.input.existing_lines)
    lines = lines.to_crs(snakemake.config["gis"]["crs"]["distance_crs"])
    lines['status'] = 'existing'
    lines["build_year"] = int(scenario_setup["simulation_years"][:4]) - 1
    lines.rename(columns={'DESIGN_VOL': 'voltage'}, inplace=True)

    if "+tdp" in scenario_setup.loc['transmission_grid']:
        # Processing planned lines for each unique year
        planned_lines = gpd.read_file(snakemake.input.planned_lines)
        planned_lines = planned_lines.to_crs(snakemake.config["gis"]["crs"]["distance_crs"])
        planned_lines.rename(columns={'DESIGN_VOL':'voltage','BUILD_YEAR': 'build_year'}, inplace=True)
        planned_lines['status'] = 'TDP'

        #drop planned lines rows where build_year is nan
        if line_config["no_build_year"] == "drop":
            planned_lines = planned_lines.dropna(subset=['build_year'])
        else:
            planned_lines['build_year'] = planned_lines['build_year'].fillna(line_config["no_build_year"])

        lines = pd.concat([lines, planned_lines])
   
    lines = build_line_topology(lines, regions)
    lines["length"] = lines['geometry'].length/1000 #in km
    lines = lines[["bus0","bus1","voltage","status","length","build_year"]]

    # Add user-defined lines
    udl = scenario_setup.loc['transmission_grid'].split("+")
    udl = [u for u in udl if u not in ['existing','tdp']]
    if len(udl) > 0:
        udl_scenario = udl[0]
        add_lines = pd.read_excel(snakemake.config["scenarios"]+'/transmission_expansion.xlsx', index_col=[0]).loc[udl_scenario].reset_index(drop=True)

            
        #model_file, sheet_name="transmission_grid", index_col=[0]).loc[udl_scenario].reset_index(drop=True)
        add_lines.rename(columns={'voltage (kV)': 'voltage','length (km)':'length'}, inplace=True)

        user_lines = pd.DataFrame(columns=['bus0', 'bus1', 'voltage', 'status', 'length', 'build_year'])    
        usr_cnt=0
        for idx, row in add_lines.iterrows():
            if row['bus0'] not in regions.index or row['bus1'] not in regions.index:
                continue
            non_zero_years = row.iloc[5:]
            non_zero_years = non_zero_years[non_zero_years != 0]
            for year in non_zero_years.index:
                for _ in range(int(non_zero_years[year])):                                   
                    user_lines.loc[usr_cnt,'bus0'] = row['bus0']
                    user_lines.loc[usr_cnt,'bus1'] = row['bus1']
                    user_lines.loc[usr_cnt,'voltage'] = row['voltage']
                    user_lines.loc[usr_cnt,'status'] = 'user_defined'
                    user_lines.loc[usr_cnt,'length'] = row['length']
                    user_lines.loc[usr_cnt,'build_year'] = year
                    usr_cnt+=1

        lines = pd.concat([lines, user_lines])

    lines['id'] = lines.index
    lines = lines.fillna(0)

    return lines

def check_centroid_in_region(regions,centroids):
    idx = regions.index[~centroids.intersects(regions['geometry'])]
    buffered_regions = regions.buffer(-200)
    boundary = buffered_regions.boundary
    for i in idx:
        # Initialize a variable to store the minimum distance
        min_distance = np.inf
        # Iterate over a range of distances along the boundary
        for d in np.arange(0, boundary[i].length, 200):
            # Interpolate a point at the current distance
            p = boundary[i].interpolate(d)
            # Calculate the distance between the centroid and the interpolated point
            distance = centroids[i].distance(p)
            # If the distance is less than the minimum distance, update the minimum distance and the closest point
            if distance < min_distance:
                min_distance = distance
                closest_point = p
        centroids[i] = closest_point
    return centroids

def build_line_topology(lines, regions):
    # Extract starting and ending points of each line
    lines = lines.explode()
    start_points = lines["geometry"].apply(lambda line: line.coords[0])
    end_points = lines["geometry"].apply(lambda line: line.coords[-1])

    # Convert start and end points to Point geometries
    start_points = start_points.apply(Point)
    end_points = end_points.apply(Point)

    # Map starting and ending points to regions
    lines["bus0"] = start_points.apply(
        lambda point: regions[regions.geometry.contains(point)].index.values[0] 
        if len(regions[regions.geometry.contains(point)].index.values) > 0 else None
    )
    lines["bus1"] = end_points.apply(
        lambda point: regions[regions.geometry.contains(point)].index.values[0] 
        if len(regions[regions.geometry.contains(point)].index.values) > 0 else None
    )
    lines['id']=range(len(lines))
    lines = lines[lines['bus0']!=lines['bus1']]
    lines = lines.dropna(subset=['bus0','bus1'])
    lines.reset_index(drop=True,inplace=True)       
    lines['bus0'], lines['bus1'] = np.sort(lines[['bus0', 'bus1']].values, axis=1).T # sort bus0 and bus1 alphabetically

    return lines


def calc_inter_region_lines(lines, line_config):
    lines=lines.copy()
    line_limits = lines.apply(lambda row: calc_line_limits(row['length'], row['voltage'], line_config), axis=1)
    lines.loc[:,'thermal_limit'], lines.loc[:,'SIL_limit'], lines.loc[:,'St_Clair_limit'] = line_limits[0].values, line_limits[1].values, line_limits[2].values
    
    def apply_n1_approximation(group):
        # If there is only one row in the group, return it as is
        if len(group) == 1:
            group.loc[:,'thermal_limit':'St_Clair_limit'] *= line_config["n1_approx_single_lines"]
            return group
        # Otherwise, remove the row with the highest St_Clair_limit
        return group.sort_values('St_Clair_limit', ascending=False).iloc[1:]

    def group_inter_region(lines, suffix=""):
        inter_region_lines = lines.groupby(['bus0','bus1','voltage']).count()['id'].reset_index().rename(columns={'id':'count'})
        inter_region_lines = inter_region_lines[inter_region_lines['bus0']!=inter_region_lines['bus1']]
        inter_region_lines['bus0'], inter_region_lines['bus1'] = np.sort(inter_region_lines[['bus0', 'bus1']].values, axis=1).T
        inter_region_lines = inter_region_lines.pivot_table(index=["bus0", "bus1"], columns="voltage", values="count", aggfunc='sum',fill_value=0).reset_index()
        inter_region_lines.columns = [col if isinstance(col, str) else str(int(col)) for col in inter_region_lines.columns]

        limits = lines[['bus0','bus1','voltage','thermal_limit','SIL_limit','St_Clair_limit']].groupby(['bus0','bus1']).sum()
        inter_region_lines = inter_region_lines.merge(limits[['thermal_limit','SIL_limit','St_Clair_limit']],on=['bus0','bus1'],how='left')

        if suffix != "":
            inter_region_lines = inter_region_lines.drop(columns=["bus0","bus1"])
            inter_region_lines.columns += suffix

        return inter_region_lines

    inter_region_lines = pd.concat(
        [
            group_inter_region(lines,""), 
            group_inter_region(lines.groupby(['bus0', 'bus1'], group_keys=False).apply(apply_n1_approximation),"_n1")
        ],axis=1)

    return inter_region_lines

def extend_topology(lines, regions, centroids):
    # get a list of lines between all adjacent regions
    adj_lines = gpd.sjoin(regions, regions, predicate='touches')['index_right'].reset_index()
    adj_lines.columns = ['bus0', 'bus1']
    adj_lines['bus0'], adj_lines['bus1'] = np.sort(adj_lines[['bus0', 'bus1']].values, axis=1).T # sort bus0 and bus1 alphabetically
    adj_lines = adj_lines.drop_duplicates(subset=['bus0', 'bus1'])

    missing_lines = adj_lines.merge(lines, on=['bus0', 'bus1'], how='left', indicator=True)
    missing_lines = missing_lines[missing_lines['_merge'] == 'left_only'][['bus0', 'bus1']]
    missing_lines['DESIGN_VOL'] = 0
    missing_lines['status'] = 'missing'
    missing_lines['geometry'] = missing_lines.apply(lambda row: LineString([centroids[row['bus0']],centroids[row['bus1']]]),axis=1)
    missing_lines = missing_lines.drop_duplicates(subset=['bus0', 'bus1'])
    lines = pd.concat([lines,missing_lines])

    return lines

def calc_line_limits(length, voltage, line_config):
    # digitised from https://www.researchgate.net/figure/The-St-Clair-curve-as-based-on-the-results-of-14-retrieved-from-15-is-used-to_fig3_318692193
    if voltage in [220, 275, 400, 765]:
        thermal = line_config['thermal'][voltage]
        SIL = line_config['SIL'][voltage]
        St_Clair = min(thermal, SIL * 53.736 * (length) ** -0.65) # length in km

    else:
        thermal = np.nan
        SIL = np.nan
        St_Clair = np.nan

    return pd.Series([thermal, SIL, St_Clair])

def build_regions(regions, line_config):
    centroids = regions['geometry'].centroid
    centroids = check_centroid_in_region(regions,centroids)
    centroids = centroids.to_crs(snakemake.config["gis"]["crs"]["geo_crs"])

    v_nom = line_config['v_nom']
    buses = (
        regions.assign(
            x=centroids.map(attrgetter('x')),
            y=centroids.map(attrgetter('y')),
            v_nom=v_nom
        )
    )
    buses.index.name='name' # ensure consistency for other scripts

    return buses, regions, centroids
def haversine_length(row, centroids):
    # Extract longitude and latitude for both points
    lon1, lat1, lon2, lat2 = map(np.radians, [
        centroids[row['bus0']].x, centroids[row['bus0']].y, 
        centroids[row['bus1']].x, centroids[row['bus1']].y
    ])

    # Calculate Haversine distance
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = np.sin(dlat / 2)**2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2)**2
    c = 2 * np.arcsin(np.sqrt(a))
    return c * 6371  # Multiply by Earth's radius in kilometers
def save_to_geopackage(gdf, layer_name, filename):
    """
    Saves a GeoDataFrame to a GeoPackage.

    Parameters:
    gdf (GeoDataFrame): The GeoDataFrame to save.
    layer_name (str): The name of the layer.
    filename (str): The path to the GeoPackage file.
    """
    gdf.to_file(filename, layer=layer_name, driver="GPKG")



def build_topology(centroids, years, line_config):
    
    years = [years[0]-1] + years
    # Reading and processing the existing lines
    lines = load_line_data(line_config)

    # Build the nextwork topology for the
    end_year_topology = calc_inter_region_lines(lines, line_config)
    end_year_topology['geometry'] = end_year_topology.apply(
        lambda row: LineString([centroids[row['bus0']], centroids[row['bus1']]]),
        axis=1
    )

    topology_derating = pd.DataFrame(columns=['bus0','bus1']+years)
    topology_derating[['bus0','bus1']] = end_year_topology[['bus0','bus1']]

    for year in years:
        # Filter lines for the current year
        year_lines = lines[lines['build_year'] <= year]

        # Create inter-region lines
        inter_region_lines = calc_inter_region_lines(year_lines, line_config)
        year_topology = end_year_topology.copy()
        year_topology.loc[:,year_topology.columns[2] :'St_Clair_limit_n1'] = 0

        for idx, row in inter_region_lines.iterrows():
            year_topology.loc[(year_topology['bus0'] == row['bus0']) & (year_topology['bus1'] == row['bus1']), 'St_Clair_limit_n1'] = row['St_Clair_limit_n1']

        topology_derating.loc[:,year] = year_topology['St_Clair_limit_n1'].div(end_year_topology['St_Clair_limit_n1'])

    network_topology = pd.concat([end_year_topology, topology_derating.loc[:,years]], axis=1)
    network_topology = gpd.GeoDataFrame(network_topology, geometry='geometry', crs = snakemake.config["gis"]["crs"]["geo_crs"])

    network_topology['length'] = network_topology.apply(lambda row: haversine_length(row, centroids), axis=1) * snakemake.config['lines']['length_factor']
    network_topology.columns = [str(col) for col in network_topology.columns]

    return lines, network_topology

if __name__ == "__main__":
    # Mock snakemake for testing if not running via Snakemake
    if 'snakemake' not in globals():
        from _helpers import mock_snakemake
        snakemake = mock_snakemake(
            'build_topology', 
            **{
                'scenario':'CNS_G_RB_CB_10_7',
            }
        )
    logging.info("Loading scenario configuration")

    scenario_setup = load_scenario_definition(snakemake)

    years = scenario_setup.loc["simulation_years"]
    if not isinstance(years, int):
        years = list(map(int, re.split(",\s*", years)))

    logging.info("Loading region GIS data")
    model_regions = str(scenario_setup.loc["regions"])
    regions = load_region_data(model_regions)

    logging.info("Building regions")
    buses, regions, centroids = build_regions(regions, snakemake.config['lines'])
    
    save_to_geojson(buses.to_crs(snakemake.config["gis"]["crs"]["geo_crs"]),snakemake.output.buses)

    logging.info("Building network topology")
    if model_regions != '1':
        lines, network_topology = build_topology(centroids, years, snakemake.config['lines'])
        save_to_geojson(network_topology, snakemake.output.lines)
    else:
        save_to_geojson(buses.to_crs(snakemake.config["gis"]["crs"]["geo_crs"]),snakemake.output.lines)
