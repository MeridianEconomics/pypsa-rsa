# SPDX-FileCopyrightText:  PyPSA-ZA2, PyPSA-ZA, PyPSA-Earth and PyPSA-Eur Authors
# # SPDX-License-Identifier: MIT

# coding: utf-8

"""
Adds electrical generators, load and existing hydro storage units to a base network.

Relevant Settings
-----------------

.. code:: yaml

    costs:
        year:
        USD_to_ZAR:
        EUR_to_ZAR:
        marginal_cost:
        dicountrate:
        emission_prices:
        load_shedding:

    electricity:
        max_hours:
        marginal_cost:
        capital_cost:
        conventional_carriers:
        co2limit:
        extendable_carriers:
        include_renewable_capacities_from_OPSD:
        estimate_renewable_capacities_from_capacity_stats:

    load:
        scale:
        ssp:
        weather_year:
        prediction_year:
        region_load:

    renewable:
        hydro:
            carriers:
            hydro_max_hours:
            hydro_capital_cost:

    lines:
        length_factor:

.. seealso::
    Documentation of the configuration file ``config.yaml`` at :ref:`costs_cf`,
    :ref:`electricity_cf`, :ref:`load_cf`, :ref:`renewable_cf`, :ref:`lines_cf`

Inputs
------
- ``model_file.xlsx``: The database to setup different scenarios based on cost assumptions for all included technologies for specific years from various sources; e.g. discount rate, lifetime, investment (CAPEX), fixed operation and maintenance (FOM), variable operation and maintenance (VOM), fuel costs, efficiency, carbon-dioxide intensity.
- ``data/Eskom EAF data.xlsx``: Hydropower plant store/discharge power capacities, energy storage capacity, and average hourly inflow by country.  Not currently used!
- ``data/eskom_pu_profiles.csv``: alternative to capacities above; not currently used!
- ``data/bundle/SystemEnergy2009_22.csv`` Hourly country load profiles produced by GEGIS
- ``resources/regions_onshore.geojson``: confer :ref:`busregions`
- ``resources/gadm_shapes.geojson``: confer :ref:`shapes`
- ``data/bundle/supply_regions/{regions}.shp``: confer :ref:`powerplants`
- ``resources/profile_{}_{regions}_{resarea}.nc``: all technologies in ``config["renewables"].keys()``, confer :ref:`renewableprofiles`.
- ``networks/base_{model_file}_{regions}.nc``: confer :ref:`base`

Outputs
-------

- ``networks/elec_{model_file}_{regions}_{resarea}.nc``:

    .. image:: ../img/elec.png
            :scale: 33 %

Description
-----------

The rule :mod:`add_electricity` ties all the different data inputs from the preceding rules together into a detailed PyPSA network that is stored in ``networks/elec.nc``. It includes:

- today"s transmission topology and transfer capacities (in future, optionally including lines which are under construction according to the config settings ``lines: under_construction`` and ``links: under_construction``),
- today"s thermal and hydro power generation capacities (for the technologies listed in the config setting ``electricity: conventional_carriers``), and
- today"s load time-series (upsampled in a top-down approach according to population and gross domestic product)

It further adds extendable ``generators`` with **zero** capacity for

- photovoltaic, onshore and AC- as well as DC-connected offshore wind installations with today"s locational, hourly wind and solar capacity factors (but **no** current capacities),
- additional open- and combined-cycle gas turbines (if ``OCGT`` and/or ``CCGT`` is listed in the config setting ``electricity: extendable_carriers``)
"""


from email import generator
import logging
import os
import geopandas as gpd
import numpy as np
import pandas as pd
import powerplantmatching as pm
import pypsa
import re
import xarray as xr
from _helpers import (
    convert_cost_units,
    configure_logging, 
    update_p_nom_max, 
    pdbcast, 
    map_component_parameters, 
    clean_pu_profiles,
    remove_leap_day,
    add_row_multi_index_df,
    drop_non_pypsa_attrs,
    find_right_index_col
)

from collections import defaultdict, Counter
from shapely.validation import make_valid
from shapely.geometry import Point
from vresutils import transfer as vtransfer
idx = pd.IndexSlice
logger = logging.getLogger(__name__)
from pypsa.descriptors import get_switchable_as_dense as get_as_dense
from pypsa.io import import_components_from_dataframe
import warnings
warnings.simplefilter(action="ignore", category=FutureWarning) # Comment out for debugging and development

def normed(s):
    return s / s.sum()

def read_and_filter_generators(file, sheet, index, filter_carriers):
    df = pd.read_excel(
        file, 
        sheet_name=sheet,
        na_values=["-"],
        index_col=[0,1]
    ).loc[index]
    return df[df["Carrier"].isin(filter_carriers)]

def append_duplicate_index(index):
    counts = {}
    new_index = []
    for idx in index:
        if index.tolist().count(idx) > 1:
            if idx in counts:
                counts[idx] += 1
            else:
                counts[idx] = 1
            new_index.append(f"{idx}*{counts[idx]}")
        else:
            new_index.append(idx)
    return new_index


def annual_costs(investment, lifetime, discount_rate, FOM):
    CRF = discount_rate / (1 - 1 / (1 + discount_rate) ** lifetime)
    return (investment * CRF + FOM).fillna(0)

def _add_missing_carriers_from_costs(n, costs, carriers):
    start_year = n.snapshots.get_level_values(0)[0] if n.multi_invest else n.snapshots[0].year
    missing_carriers = pd.Index(carriers).difference(n.carriers.index)
    if missing_carriers.empty: return

    emissions = costs.loc[("co2_emissions",missing_carriers),start_year]
    emissions.index = emissions.index.droplevel(0)
    n.madd("Carrier", missing_carriers, co2_emissions=emissions)
    
def load_costs(model_file, cost_scenario):
    """
    set all asset costs tab in the model file
    """

    costs = pd.read_excel(
        model_file, 
        sheet_name = "costs",
        index_col = [0,2,1],
    ).sort_index().loc[cost_scenario]

    costs.drop("source", axis=1, inplace=True)
    
    # Interpolate for years in config file but not in cost_data excel file
    ext_years = n.investment_periods if n.multi_invest else [n.snapshots[0].year]
    ext_years_array = np.array(ext_years)
    missing_year = ext_years_array[~np.isin(ext_years_array,costs.columns)]
    if len(missing_year) > 0:
        for i in missing_year: 
            costs.insert(0,i,np.nan) # add columns of missing year to dataframe
        costs_tmp = costs.drop("unit", axis=1).sort_index(axis=1)
        costs_tmp = costs_tmp.interpolate(axis=1)
        costs = pd.concat([costs_tmp, costs["unit"]], ignore_index=False, axis=1)

    # correct units to MW and ZAR
    costs_yr = costs.columns.drop("unit")

    costs = convert_cost_units(costs, snakemake.config["costs"]["USD_to_ZAR"], snakemake.config["costs"]["EUR_to_ZAR"])
    
    full_costs = pd.DataFrame(
        index = pd.MultiIndex.from_product(
            [
                costs.index.get_level_values(0).unique(),
                costs.index.get_level_values(1).unique()]),
        columns = costs.columns
    )
    # full_costs adds default values when missing from costs table
    for default in costs.index.get_level_values(0):
        full_costs.loc[costs.loc[(default, slice(None)),:].index, :] = costs.loc[(default, slice(None)),:]
        full_costs.loc[(default, slice(None)), costs_yr] = full_costs.loc[(default, slice(None)), costs_yr].fillna(snakemake.config["costs"]["defaults"][default])
    full_costs = full_costs.fillna("default")
    costs = full_costs.copy()

    # Get entries where FOM is specified as % of CAPEX
    fom_perc_capex=costs.loc[costs.unit.str.contains("%/year") == True, costs_yr]
    fom_perc_capex_idx=fom_perc_capex.index.get_level_values(1)

    add_costs = pd.DataFrame(
        index = pd.MultiIndex.from_product([["capital_cost","marginal_cost"],costs.loc["FOM"].index]),
        columns = costs.columns
    )
    
    costs = pd.concat([costs, add_costs],axis=0)
    costs.loc[("FOM",fom_perc_capex_idx), costs_yr] = (costs.loc[("investment", fom_perc_capex_idx),costs_yr]).values/100.0
    costs.loc[("FOM",fom_perc_capex_idx), "unit"] = costs.loc[("investment", fom_perc_capex_idx),"unit"].values

    capital_costs = annual_costs(
        costs.loc["investment", costs_yr],
        costs.loc["lifetime", costs_yr], 
        costs.loc["discount_rate", costs_yr],
        costs.loc["FOM", costs_yr],
    )

    costs.loc["capital_cost", costs_yr] = capital_costs.fillna(0).values
    costs.loc["capital_cost","unit"] = "R/MWe"

    vom = costs.loc["VOM", costs_yr].fillna(0)
    fuel = (costs.loc["fuel", costs_yr] / costs.loc["efficiency", costs_yr]).fillna(0)

    costs.loc[("marginal_cost", vom.index), costs_yr] = vom.values
    costs.loc[("marginal_cost", fuel.index), costs_yr] += fuel.values
    costs.loc["marginal_cost","unit"] = "R/MWhe"

    max_hours = snakemake.config["electricity"]["max_hours"]
    costs.loc[("capital_cost","battery"), :] = costs.loc[("capital_cost","battery inverter"),:]
    costs.loc[("capital_cost","battery"), costs_yr] += max_hours["battery"]*costs.loc[("capital_cost", "battery storage"), costs_yr]
    
    return costs



# def process_eaf(eskom_data, ref_yrs, snapshots, carriers):
#     eaf = eskom_data["EAF %"]/100#
#     eaf = eaf.loc[eaf.index.get_level_values(0).year.isin(ref_yrs)]  
#     eaf_m = eaf.groupby(["station", eaf.index.get_level_values(0).month]).mean().unstack(level=0)[carriers]

#     eaf_h = pd.DataFrame(1, index = snapshots, columns = eaf_m.columns)
#     eaf_h = eaf_m.loc[eaf_h.index.month].reset_index(drop=True).set_index(eaf_h.index)
#     eaf_y = eaf_h.groupby(eaf_h.index.year).mean()
#     return eaf_h, eaf_y

def proj_eaf_override(eaf_hrly, projections, snapshots, include = "_EAF", exclude = "extendable"):
    eaf_yrly = eaf_hrly.groupby(eaf_hrly.index.year).mean()
    proj_eaf = projections.loc[(projections.index.str.contains(include) & ~projections.index.str.contains(exclude)), snapshots.year.unique()]
    proj_eaf.index = proj_eaf.index.str.replace(include,"")

    # remove decom_stations
    proj_eaf = proj_eaf[proj_eaf.index.isin(eaf_yrly.columns)]
    scaling = proj_eaf.T.div(eaf_yrly[proj_eaf.index], axis="columns", level="year").fillna(1)

    for y in snapshots.year.unique():
        eaf_hrly.loc[str(y), scaling.columns] *= scaling.loc[y, :]  

    return eaf_hrly

def get_eskom_eaf(ref_yrs, snapshots, grouped = False):
    # Add plant availability based on actual Eskom data provided
    eskom_data  = pd.read_excel(
        snakemake.input.existing_generators_eaf, 
        sheet_name="eskom_data", 
        na_values=["-"],
        index_col=[1,0],
        parse_dates=True
    )

    eaf = (eskom_data["EAF %"]/100).unstack(level=0)

    eaf = eaf.loc[eaf.index.year.isin(ref_yrs)]
    eaf_mnthly = eaf.groupby(eaf.index.month).mean()

    eaf_hrly = pd.DataFrame(1, index = snapshots, columns = eaf_mnthly.columns)
    eaf_hrly = eaf_mnthly.loc[eaf_hrly.index.month].reset_index(drop=True).set_index(eaf_hrly.index) 

    return eaf_hrly

def add_generator_availability(n, projections):
    config = snakemake.config["electricity"]["conventional_generators"]
    fix_ref_years = config["fix_ref_years"]
    ext_ref_years = config["ext_ref_years"]
    conv_carriers = config["carriers"]
    ext_unit_ref = config["extendable_reference"]
    conv_extendable = snakemake.config["electricity"]["extendable_carriers"]["Generator"]
    
    snapshots = n.snapshots.get_level_values(1) if n.multi_invest else n.snapshots
    
    eskom_fix_eaf = get_eskom_eaf(fix_ref_years, snapshots)
    eskom_fix_eaf = proj_eaf_override(eskom_fix_eaf, projections, snapshots, include = "_EAF", exclude = "extendable")
    
    eskom_ext_eaf = get_eskom_eaf(ext_ref_years, snapshots)
    eskom_ext_eaf = proj_eaf_override(eskom_ext_eaf, projections, snapshots, include = "_extendable_EAF", exclude = "NA")


    fix_i, fix_i_missing, eaf = get_eaf(n, eskom_data, snapshots, conv_carriers, p_nom_extendable=False)
    fix_st_eaf_h, fix_st_eaf_y = process_eaf(eskom_data, fix_ref_years, snapshots, eaf.index.get_level_values(1).unique())
    fix_st_eaf_h = proj_eaf_override(projections, snapshots, fix_st_eaf_h, fix_st_eaf_y, include = "_EAF", exclude = "extendable")

    # map back to station units
    fix_eaf_h = pd.DataFrame(index = snapshots, columns = fix_i.drop(fix_i_missing))
    # Using .loc indexer to align data based on the mapping from fix_i_station
    fix_eaf_h = fix_st_eaf_h.loc[:, fix_i_station[fix_i.drop(fix_i_missing).values]].copy()
    fix_eaf_h.columns = fix_i.drop(fix_i_missing).values

    # Extendable generators and missing fixed generators
    # check if carriers from fix_i_missing_car are all in car_extendable
    for carrier in fix_i_missing_car[~fix_i_missing_car.isin(conv_extendable)].unique():
        conv_extendable.append(carrier)
    conv_extendable = [c for c in conv_extendable if c in ext_unit_ref]

    car_eaf_h, car_eaf_y = process_eaf(eskom_data, ext_ref_years, snapshots, ext_unit_ref.values())
    car_eaf_h.columns, car_eaf_y.columns = ([k for k, v in ext_unit_ref.items() if v in ext_unit_ref.values()] for _ in range(2))
    car_eaf_h = proj_eaf_override(projections, snapshots, car_eaf_h, car_eaf_y, include = "_extendable_EAF", exclude="NA")
    
    ext_eaf_h = pd.DataFrame(index = snapshots, columns = ext_i.append(fix_i_missing_car.index))
    for g in ext_i.append(fix_i_missing_car.index):
        carrier = n.generators.loc[g, "carrier"]
        if carrier in car_eaf_h.columns:
            ext_eaf_h[g] = car_eaf_h[carrier].values
    ext_eaf_h = ext_eaf_h.fillna(1) # if reference not specified

    eaf_h = pd.concat([fix_eaf_h, ext_eaf_h],axis=1).fillna(1)
    eaf_h[eaf_h >1] = 1
    eaf_h.index = n.snapshots
    n.generators_t.p_max_pu[eaf_h.columns] = eaf_h 

def add_min_stable_levels(n):
    min_st_lvl = snakemake.config["electricity"]["min_stable_levels"]

    static = [k for k in min_st_lvl.keys() if min_st_lvl[k][1] == "static"]
    dynamic = [k for k in min_st_lvl.keys() if min_st_lvl[k][1] == "dynamic"]

    for carrier in static:
        gen_lst = n.generators[n.generators.carrier == carrier].index
        n.generators_t.p_min_pu[gen_lst] = min_st_lvl[carrier][0]

    for carrier in dynamic:
        gen_lst = n.generators[n.generators.carrier == carrier].index
        p_max_pu = get_as_dense(n, "Generator", "p_max_pu")[gen_lst]
        n.generators_t.p_min_pu[gen_lst] = (min_st_lvl[carrier][0]*p_max_pu).fillna(0)

 ## Attach components
# ### Load

def attach_load(n, annual_demand):
    load = pd.read_csv(snakemake.input.load,index_col=[0],parse_dates=True)
    
    annual_demand = annual_demand.drop("unit")*1e6
    profile_demand = normed(remove_leap_day(load.loc[str(snakemake.config["years"]["reference_demand_year"]),"system_energy"]))
    
    if n.multi_invest:
        demand=pd.Series(0,index=n.snapshots)
        for y in n.investment_periods:
            demand.loc[y]=profile_demand.values*annual_demand[y]
    else:
        demand = pd.Series(profile_demand.values*annual_demand[n.snapshots[0].year], index = n.snapshots)

    if snakemake.wildcards.regions == "1-supply":
        n.add("Load", n.buses.index,
            bus="RSA",
            p_set=demand)
    else:
        n.madd("Load", n.buses.index,
            bus=n.buses.index,
            p_set=pdbcast(demand, normed(n.buses[snakemake.config["electricity"]["demand_disaggregation"]])))


### Generate pu profiles for other_re based on Eskom data
def generate_eskom_re_profiles(n):
    ext_years = n.investment_periods if n.multi_invest else [n.snapshots[0].year]
    carriers = snakemake.config["electricity"]["renewable_generators"]["carriers"]
    ref_years = snakemake.config["years"]["reference_weather_years"]

    if snakemake.config["enable"]["use_excel_wind_solar"][0]:
        carriers = [elem for elem in carriers if elem not in ["wind","solar_pv"]]

    eskom_data = (
        pd.read_csv(
            snakemake.input.eskom_profiles,skiprows=[1], 
            index_col=0,parse_dates=True
        )
        .resample("1h").mean()
    )

    eskom_data = remove_leap_day(eskom_data)
    eskom_profiles = pd.DataFrame(0, index=n.snapshots, columns=carriers)

    for carrier in carriers:
        weather_years = ref_years[carrier].copy()
        if n.multi_invest:
            weather_years *= int(np.ceil(len(ext_years) / len(weather_years)))

        for cnt, y in enumerate(ext_years):
            y = y if n.multi_invest else str(y)
            eskom_profiles.loc[y, carrier] = (eskom_data.loc[str(weather_years[cnt]), carrier]
                                            .clip(lower=0., upper=1.)).values
    return eskom_profiles


def generate_excel_wind_solar_profiles(n):
    ref_years = snakemake.config["years"]["reference_weather_years"]
    snapshots = n.snapshots.get_level_values(1) if n.multi_invest else n.snapshots
    profiles=pd.DataFrame(index=pd.MultiIndex.from_product([["wind", "solar_pv"], snapshots], names=["Generator", "snapshots"]), columns=n.buses.index)
    ext_years = n.investment_periods if n.multi_invest else [n.snapshots[0].year]

    for carrier in ["wind", "solar_pv"]:
        raw_profiles= (
            pd.read_excel(snakemake.config["enable"]["use_excel_wind_solar"][1],
            sheet_name=snakemake.wildcards.regions+"_"+carrier+"_pu",
            skiprows=[1], 
            index_col=0,parse_dates=True)
            .resample("1h").mean()
        )
        raw_profiles = remove_leap_day(raw_profiles)
        raw_profiles = raw_profiles.loc[ref_years[carrier]]
        profiles = extend_reference_data(n, raw_profiles, snapshots)


        # weather_years = ref_years[carrier].copy()
        # if n.multi_invest:
        #     weather_years *= int(np.ceil(len(ext_years) / len(weather_years)))

        # # Use the default RSA hourly data (from Eskom) and extend to multiple weather years
        # for cnt, y in enumerate(ext_years):    
        #     profiles.loc[(carrier, str(y)), n.buses.index] = (
        #         raw_profiles.loc[str(weather_years[cnt]),n.buses.index]
        #         .clip(lower=0., upper=1.)
        #     ).values
    # if n.multi_invest:
    #     profiles["periods"] = profiles.index.get_level_values(1).year
    #     profiles = profiles.reset_index().set_index(["Generator", "periods", "snapshots"])
    return profiles


### Set line costs
def update_transmission_costs(n, costs, length_factor=1.0, simple_hvdc_costs=False):
    # Currently only average transmission costs are implemented
    n.lines["capital_cost"] = (
        n.lines["length"] * length_factor * costs.loc[("capital_cost","HVAC_overhead"), costs.columns.drop("unit")].mean()
    )

    if n.links.empty:
        return

    dc_b = n.links.carrier == "DC"
    # If there are no "DC" links, then the "underwater_fraction" column
    # may be missing. Therefore we have to return here.
    # TODO: Require fix
    if n.links.loc[n.links.carrier == "DC"].empty:
        return

    if simple_hvdc_costs:
        hvdc_costs = (
            n.links.loc[dc_b, "length"]
            * length_factor
            * costs.loc[("capital_cost","HVDC_overhead"),:].mean()
        )
    else:
        hvdc_costs = (
            n.links.loc[dc_b, "length"]
            * length_factor
            * (
                (1.0 - n.links.loc[dc_b, "underwater_fraction"])
                * costs.loc[("capital_cost","HVDC_overhead"),:].mean()
                + n.links.loc[dc_b, "underwater_fraction"]
                * costs.loc[("capital_cost","HVDC_submarine"),:].mean()
            )
            + costs.loc[("capital_cost","HVDC inverter_pair"),:].mean()
        )
    n.links.loc[dc_b, "capital_cost"] = hvdc_costs




def load_components_from_model_file(model_file, model_setup, carriers, start_year, config):
    """
    Load components from a model file based on specified filters and configurations.

    Args:
        model_file: The file path to the model file.
        model_setup: The model setup object.
        carriers: A list of carriers to filter the generators.
        start_year: The start year for the components.
        config: A dictionary containing configuration settings.

    Returns:
        A DataFrame containing the loaded components.
    """
    conv_gens = read_and_filter_generators(model_file, "existing_conventional", model_setup.existing_eskom, carriers)
    re_gens = read_and_filter_generators(model_file, "existing_renewables", model_setup.existing_non_eskom, carriers)

    conv_gens["apply_grouping"] = config["conventional_generators"]["apply_grouping"]
    re_gens["apply_grouping"] = config["renewable_generators"]["apply_grouping"]
    re_gens.set_index((re_gens["Model Key"] + "_" + re_gens["Carrier"]).values,inplace=True)

    gens = pd.concat([conv_gens, re_gens])
    gens = map_component_parameters(gens, start_year)
    gens = gens.query("(p_nom > 0) & x.notnull() & y.notnull() & (lifetime >= 0)")
    
    return gens

def map_components_to_buses(component_df, regions, crs_config):
    """
    Associate every generator/storage_unit with the bus of the region based on GPS coords.

    Args:
        component_df: A DataFrame containing generator/storage_unit data.
        regions: The file path to the regions shapefile.
        crs_config: A dictionary containing coordinate reference system configurations.

    Returns:
        A DataFrame with the generators associated with their respective bus.
    """

    regions_gdf = gpd.read_file(regions).to_crs(snakemake.config["crs"]["distance_crs"]).set_index("name")
    gps_gdf = gpd.GeoDataFrame(
        geometry=gpd.GeoSeries([Point(o.x, o.y) for o in component_df[["x", "y"]].itertuples()],
        index=component_df.index, 
        crs=crs_config["geo_crs"]
    ).to_crs(crs_config["distance_crs"]))
    joined = gpd.sjoin(gps_gdf, regions_gdf, how="left", predicate="within")
    right_index_col = find_right_index_col(joined)
    component_df["bus"] = joined[right_index_col].copy()

    if empty_bus := list(component_df[~component_df["bus"].notnull()].index):
        logger.warning(f"Dropping generators/storage units with no bus assignment {empty_bus}")
        component_df = component_df[component_df["bus"].notnull()]

    return component_df

def group_components(component_df):
    """
    Apply grouping of similar carrier if specified in snakemake config.

    Args:
        component_df: A DataFrame containing generator/storage_unit data.

    Returns:
        A tuple containing two DataFrames: grouped_df, non_grouped_df
    """
    params = ["bus", "carrier", "lifetime", "p_nom", "efficiency", "ramp_limit_up", "ramp_limit_down", "marginal_cost", "capital_cost"]
    param_cols = [p for p in params if p not in ["bus","carrier","p_nom"]]

    filtered_df = component_df[component_df["apply_grouping"]].copy().fillna(0)

    grouped_df = pd.DataFrame(index=filtered_df.groupby(["Grouping", "carrier", "bus"]).sum().index, columns = param_cols)
    grouped_df["p_nom"] = filtered_df.groupby(["Grouping", "carrier", "bus"]).sum()["p_nom"]

    for param in [p for p in params if p not in ["bus","carrier","p_nom"]]:
        weighted_sum = filtered_df.groupby(["Grouping", "carrier", "bus"]).apply(lambda x: (x[param] * x["p_nom"]).sum())
        total_p_nom = filtered_df.groupby(["Grouping", "carrier", "bus"])["p_nom"].sum()
        weighted_average = weighted_sum / total_p_nom 
        grouped_df.loc[weighted_average.index, param] = weighted_average.values
    
    rename_idx = grouped_df.index.get_level_values(2) +  "-" + grouped_df.index.get_level_values(1) +  "_" + grouped_df.index.get_level_values(0)
    grouped_df = grouped_df.reset_index(level=[1,2]).replace(0, np.nan).set_index(rename_idx) # replace 0 with nan to ignore in pypsa

    non_grouped_df = component_df[~component_df["apply_grouping"]][params].copy()

    return grouped_df, non_grouped_df

def group_pu_profiles(pu_profiles, component_df):
    years = pu_profiles.index.get_level_values(1).year.unique()
    pu_mul_p_nom = pu_profiles * component_df["p_nom"]

    filtered_df = component_df[component_df["apply_grouping"]].copy().fillna(0)

    for bus in filtered_df.bus.unique():
        for carrier in filtered_df.carrier.unique():
            carrier_list = filtered_df[(filtered_df["carrier"] == carrier) & (filtered_df["bus"] == bus)].index
            for y in years:
                active = carrier_list[(component_df.loc[carrier_list, "lifetime"] - (y-years[0]))>=0]
                if len(active)>0:
                    key_list = filtered_df.loc[active, "Grouping"]
                    for key in key_list.unique():
                        active_key = active[filtered_df.loc[active, "Grouping"] == key]
                        pu_profiles.loc[(slice(None), str(y)), bus + "-" + carrier + "_" + key] = pu_mul_p_nom.loc[(slice(None), str(y)), active_key].sum(axis=1) / component_df.loc[active_key, "p_nom"].sum()
            pu_profiles.drop(columns = carrier_list, inplace=True)

    return pu_profiles.fillna(0)

def extend_reference_data(n, ref_data, snapshots):
    ext_years = snapshots.year.unique()
    extended_data = pd.DataFrame(0, index=snapshots, columns=ref_data.columns)
    ref_years = ref_data.index.year.unique()

    for _ in range(int(np.ceil(len(ext_years) / len(ref_years)))-1):
        ref_data = pd.concat([ref_data, ref_data],axis=0)

    extended_data.iloc[:] = ref_data.iloc[range(len(extended_data)),:].values

    return extended_data

def generate_existing_wind_solar_profiles(n, gens, ref_data, snapshots, pu_profiles):
  
    for carrier in ["wind", "solar_pv"]:
        pu = pd.read_excel(
            ref_data,
            sheet_name=f"existing_{carrier}_pu",
            index_col=0,
            parse_dates=True,
        )

        pu = remove_leap_day(pu)
        mapping = gens.loc[(gens["Model Key"] != np.nan) & (gens["carrier"] == carrier),"Model Key"]
        mapping = pd.Series(mapping.index,index=mapping.values)
        pu = pu[mapping.index]
        pu.columns = mapping[pu.columns].values
        pu = extend_reference_data(n, pu, snapshots)
        pu_profiles.loc["max", pu.columns] = pu.values
        pu_profiles.loc["min", pu.columns] = 0.95*pu.values # Existing REIPPP take or pay constraint (100% can cause instabilitites)

    return pu_profiles

def generate_extendable_wind_solar_profiles(n, gens, ref_data, snapshots, pu_profiles):
  
    for carrier in ["wind", "solar_pv"]:
        pu = pd.read_excel(
            ref_data,
            sheet_name=f"existing_{carrier}_pu",
            index_col=0,
            parse_dates=True,
        )

        pu = remove_leap_day(pu)
        mapping = gens.loc[(gens["Model Key"] != np.nan) & (gens["carrier"] == carrier),"Model Key"]
        mapping = pd.Series(mapping.index,index=mapping.values)
        pu = pu[mapping.index]
        pu.columns = mapping[pu.columns].values
        pu = extend_reference_data(n, pu, snapshots)
        pu_profiles.loc["max", pu.columns] = pu.values
        pu_profiles.loc["min", pu.columns] = 0.95*pu.values # Existing REIPPP take or pay constraint (100% can cause instabilitites)

    return pu_profiles


# # Generators
def attach_existing_generators(n, costs, model_setup, model_file):
    # setup carrier info
    config = snakemake.config["electricity"]["conventional_generators"]
    fix_ref_years = config["fix_ref_years"]
    ext_ref_years = config["ext_ref_years"]
    conv_carriers = config["carriers"]
    ext_unit_ref = config["extendable_reference"]
    conv_extendable = snakemake.config["electricity"]["extendable_carriers"]["Generator"]
    conv_carriers = snakemake.config["electricity"]["conventional_generators"]["carriers"]
    re_carriers = snakemake.config["electricity"]["renewable_generators"]["carriers"]
    carriers = conv_carriers + re_carriers
    
    ext_years = n.investment_periods if n.multi_invest else [n.snapshots[0].year]
    ref_years = snakemake.config["years"]["reference_weather_years"]
    
    start_year = n.snapshots.get_level_values(0)[0] if n.multi_invest else n.snapshots[0].year
    snapshots = n.snapshots.get_level_values(1) if n.multi_invest else n.snapshots
    
    # load generators from model file
    gens = load_components_from_model_file(model_file, model_setup, carriers, start_year, snakemake.config["electricity"])
    gens = map_components_to_buses(gens, snakemake.input.supply_regions, snakemake.config["crs"])


    pu_profiles = pd.DataFrame(index = pd.MultiIndex.from_product([["max", "min"], snapshots], names=["profile", "snapshots"]), columns = gens.index)
    pu_profiles.loc["max"] = 1 
    pu_profiles.loc["min"] = 0

    # Monthly average EAF for conventional plants from Eskom  
    eskom_conv_pu = get_eskom_eaf(fix_ref_years, snapshots)
    eskom_conv_pu = proj_eaf_override(eskom_conv_pu, projections, snapshots, include = "_EAF", exclude = "extendable")
    eskom_carriers = [carrier for carrier in conv_carriers if carrier not in ["nuclear", "hydro", "hydro_import"]]
    for col in gens.query("Grouping == 'eskom' & carrier in @eskom_carriers").index:
        pu_profiles.loc["max", col] = eskom_conv_pu[col.split("*")[0]].values

    # Hourly data from Eskom data portal
    eskom_re_pu = generate_eskom_re_profiles(n)
    eskom_re_carriers = eskom_re_pu.columns
    for col in gens.query("carrier in @eskom_re_carriers").index:
        pu_profiles.loc["max", col] = eskom_re_pu[gens.loc[col, "carrier"]].values

    # Wind and solar profiles if not using Eskom data portal
    if snakemake.config["enable"]["use_excel_wind_solar"][0]:
        ref_data = pd.ExcelFile(snakemake.config["enable"]["use_excel_wind_solar"][1])
        pu_profiles = generate_existing_wind_solar_profiles(n, gens, ref_data, snapshots, pu_profiles)

    pu_profiles = group_pu_profiles(pu_profiles, gens) #includes both grouped an non-grouped generators
    grouped_gens, non_grouped_gens = group_components(gens)
    grouped_gens["build_year"], grouped_gens["p_nom_extendable"] = start_year, False
    non_grouped_gens["build_year"], non_grouped_gens["p_nom_extendable"] = start_year, False

    n.import_components_from_dataframe(non_grouped_gens, "Generator")
    n.import_components_from_dataframe(grouped_gens, "Generator")

    n.generators_t.p_max_pu = pu_profiles.loc["max"]
    n.generators_t.p_min_pu = pu_profiles.loc["min"]
    
    _add_missing_carriers_from_costs(n, costs, gens.carrier.unique())


def set_default_extendable_params(c, bus_carrier_years, **config):
    default_param = [
                "bus",
                "p_nom_extendable",
                "carrier",
                "build_year",
                "lifetime",
                "capital_cost",
                "marginal_cost",
    ]
    if c == "Generator":
        default_param += ["efficiency"]
    elif c == "StorageUnit":
        default_param += ["max_hours", "efficiency_store", "efficiency_dispatch"]

    component_df = pd.DataFrame(index = bus_carrier_years, columns = default_param)

    component_df["p_nom_extendable"] = True
    component_df["bus"] = component_df.index.str.split("-").str[0]
    component_df["carrier"] = component_df.index.str.split("-").str[1]
    component_df["build_year"] = component_df.index.str.split("-").str[2].astype(int)

    for param in ["lifetime", "capital_cost", "marginal_cost", "efficiency"]:
        component_df[param] =  component_df.apply(lambda row: costs.loc[(param, row["carrier"]), row["build_year"]], axis=1)

    if c == "StorageUnit":
        component_df["cyclic_state_of_charge"] = True
        component_df["cyclic_state_of_charge_per_period"] = True
        component_df["efficiency_store"] = component_df["efficiency"]**0.5
        component_df["efficiency_dispatch"] = component_df["efficiency"]**0.5
        component_df["max_hours"] = component_df["carrier"].map(config["max_hours"])
        component_df = component_df.drop("efficiency", axis=1)
    return component_df

def attach_extendable_generators(n, costs):
    config = snakemake.config["electricity"]
    carriers = config["extendable_carriers"]["Generator"]
    ext_years = n.investment_periods if n.multi_invest else [n.snapshots[0].year]

    bus_carrier_years = [f"{bus}-{carrier}-{year}" for bus in n.buses.index for carrier in carriers for year in ext_years]
    gens = set_default_extendable_params("Generator", bus_carrier_years)
    #gens = drop_non_pypsa_attrs(n, "Generator", gens)
    n.import_components_from_dataframe(gens, "Generator")
    n.generators["plant_name"] = n.generators.index.str.split("*").str[0]
    _add_missing_carriers_from_costs(n, costs, carriers)

def attach_existing_storage(n, model_setup, model_file): 
    carriers = ["phs", "battery"]
    start_year = n.snapshots.get_level_values(0)[0] if n.multi_invest else n.snapshots[0].year
    
    storage = load_components_from_model_file(model_file, model_setup, carriers, start_year, snakemake.config["electricity"])
    storage = map_components_to_buses(storage, snakemake.input.supply_regions, snakemake.config["crs"])

    max_hours_col = [col for col in storage.columns if "_max_hours" in col]
    efficiency_col = [col for col in storage.columns if "_efficiency" in col]

    storage["max_hours"] = storage[max_hours_col].sum(axis=1)
    storage["efficiency_store"] = storage[efficiency_col].sum(axis=1)**0.5
    storage["efficiency_dispatch"] = storage[efficiency_col].sum(axis=1)**0.5
    storage["cyclic_state_of_charge"], storage["p_nom_extendable"] = True, False
    
    storage = drop_non_pypsa_attrs(n, "StorageUnit", storage)
    n.import_components_from_dataframe(storage, "StorageUnit")

def attach_extendable_storage(n, costs):
    config = snakemake.config["electricity"]
    carriers = config["extendable_carriers"]["StorageUnit"]
    ext_years = n.investment_periods if n.multi_invest else [n.snapshots[0].year]
    _add_missing_carriers_from_costs(n, costs, carriers)

    bus_carrier_years = [f"{bus}-{carrier}-{year}" for bus in n.buses.index for carrier in carriers for year in ext_years]
    storage = set_default_extendable_params("StorageUnit", bus_carrier_years, **config)
    
    n.import_components_from_dataframe(storage, "StorageUnit")

def add_co2limit(n):
    n.add("GlobalConstraint", "CO2Limit",
          carrier_attribute="co2_emissions", sense="<=",
          constant=snakemake.config["electricity"]["co2limit"])

def add_emission_prices(n, emission_prices=None, exclude_co2=False):
    if emission_prices is None:
        emission_prices = snakemake.config["costs"]["emission_prices"]
    if exclude_co2: emission_prices.pop("co2")
    ep = (pd.Series(emission_prices).rename(lambda x: x+"_emissions") * n.carriers).sum(axis=1)
    n.generators["marginal_cost"] += n.generators.carrier.map(ep)
    n.storage_units["marginal_cost"] += n.storage_units.carrier.map(ep)

def add_peak_demand_hour_without_variable_feedin(n):
    new_hour = n.snapshots[-1] + pd.Timedelta(hours=1)
    n.set_snapshots(n.snapshots.append(pd.Index([new_hour])))

    # Don"t value new hour for energy totals
    n.snapshot_weightings[new_hour] = 0.

    # Don"t allow variable feed-in in this hour
    n.generators_t.p_max_pu.loc[new_hour] = 0.

    n.loads_t.p_set.loc[new_hour] = (
        n.loads_t.p_set.loc[n.loads_t.p_set.sum(axis=1).idxmax()]
        * (1.+snakemake.config["electricity"]["SAFE_reservemargin"])
    )

def add_nice_carrier_names(n):

    carrier_i = n.carriers.index
    nice_names = (
        pd.Series(snakemake.config["plotting"]["nice_names"])
        .reindex(carrier_i)
        .fillna(carrier_i.to_series().str.title())
    )
    n.carriers["nice_name"] = nice_names
    colors = pd.Series(snakemake.config["plotting"]["tech_colors"]).reindex(carrier_i)
    if colors.isna().any():
        missing_i = list(colors.index[colors.isna()])
        logger.warning(
            f"tech_colors for carriers {missing_i} not defined " "in config."
        )
    n.carriers["color"] = colors


if __name__ == "__main__":
    if "snakemake" not in globals():
        from _helpers import mock_snakemake
        snakemake = mock_snakemake(
            "add_electricity", 
            **{
                "model_file":"grid-2040",
                "regions":"11-supply",
                "resarea":"redz",
            }
        )
    model_file = pd.ExcelFile(snakemake.input.model_file)
    model_setup = (
        pd.read_excel(
            model_file, 
            sheet_name="model_setup",
            index_col=[0])
            .loc[snakemake.wildcards.model_file]
    )

    projections = (
        pd.read_excel(
            model_file, 
            sheet_name="projected_parameters",
            index_col=[0,1])
            .loc[model_setup["projected_parameters"]]
    )

    #opts = snakemake.wildcards.opts.split("-")
    n = pypsa.Network(snakemake.input.base_network)
    costs = load_costs(model_file, model_setup.costs)

    #wind_solar_profiles = xr.open_dataset(snakemake.input.wind_solar_profiles).to_dataframe()
    eskom_profiles = generate_eskom_re_profiles(n)

    attach_load(n, projections.loc["annual_demand",:])
    if snakemake.wildcards.regions!="1-supply":
        update_transmission_costs(n, costs)
    attach_existing_generators(n, costs, model_setup, model_file)
    attach_extendable_generators(n, costs)
    attach_existing_storage(n, model_setup, model_file)
    attach_extendable_storage(n, costs) 

    add_nice_carrier_names(n)
    n.export_to_netcdf(snakemake.output[0])
