# SPDX-FileCopyrightText:  PyPSA-RSA, PyPSA-ZA, PyPSA-Earth and PyPSA-Eur Authors
# # SPDX-License-Identifier: MIT

# coding: utf-8

"""
Adds fixed and extendable components to the base network. The primary functions run inside main are:

    attach_load
    attach_fixed_generators
    attach_extendable_generators
    attach_fixed_storage
    attach_extendable_storage

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

import geopandas as gpd
import logging
logger = logging.getLogger(__name__)
import numpy as np
import pandas as pd
import pypsa
from pypsa.descriptors import get_switchable_as_dense as get_as_dense, get_activity_mask
from pypsa.io import import_components_from_dataframe
import os
from shapely.geometry import Point
import xarray as xr
import warnings
warnings.simplefilter(action="ignore") # Comment out for debugging and development


from _helpers import (
    add_missing_carriers,
    add_noise,
    adjust_by_p_max_pu,
    apply_default_attr,
    convert_cost_units,
    drop_non_pypsa_attrs,
    get_carriers_from_model_file,
    get_investment_periods,
    get_snapshots,
    get_start_year,
    initial_ramp_rate_fix,
    load_disaggregate, 
    map_component_parameters, 
    normed,
    read_and_filter_generators,
    remove_leap_day,
    single_year_network_copy,
    load_scenario_definition
)

"""
********************************************************************************
    Cost related functions
********************************************************************************
"""
def annualise_costs(investment, lifetime, discount_rate, FOM):
    """
    Annualises the costs of an investment over its lifetime.The costs are annualised using the 
    Capital Recovery Factor (CRF) formula.

    Args:
    - investment: The overnight investment cost.
    - lifetime: The lifetime of the investment.
    - discount_rate: The discount rate used for annualisation.
    - FOM: The fixed operating and maintenance costs.

    Returns:
    A Series containing the annualised costs.
    """
    CRF = discount_rate / (1 - 1 / (1 + discount_rate) ** lifetime)
    return (investment * CRF + FOM).fillna(0)

def load_extendable_parameters(n, scenario_setup, snakemake):
    """
    set all asset costs tab in the model file
    """
    defaults = snakemake.config["electricity"]["extendable_parameters"]["defaults"]

    param_mapping = pd.read_excel(
        os.path.join(scenario_setup["sub_path"],"extendable_technologies.xlsx"), 
        sheet_name = "parameter_mapping",
        index_col = [0,1],
    ).loc[scenario_setup["extendable_parameters"]]

    param_matrix = pd.read_excel(
        os.path.join(scenario_setup["sub_path"],"extendable_technologies.xlsx"), 
        sheet_name = "parameters",
        index_col = [0,2,1],
    ).sort_index()

    param = pd.DataFrame(index = pd.MultiIndex.from_product([param_mapping.columns, param_mapping.index]), columns = param_matrix.columns)
    
    for idx in param.index:
        mapping_ = param_mapping.loc[idx[1], idx[0]]
        if pd.isna(mapping_):
            # get default value from config file
            param.loc[idx, :] = defaults[idx[0]]
        else:
            param.loc[idx, :] = param_matrix.loc[(mapping_, idx[0], idx[1]), :].values

    param.drop("source", axis=1, inplace=True)
    
    # Interpolate for years in config file but not in cost_data excel file
    ext_years = get_investment_periods(n.snapshots, n.multi_invest)
    ext_years_array = np.array(ext_years)
    missing_year = ext_years_array[~np.isin(ext_years_array,param.columns)]
    if len(missing_year) > 0:
        for i in missing_year: 
            param.insert(0,i,np.nan) # add columns of missing year to dataframe
        param_tmp = param.drop("unit", axis=1).sort_index(axis=1)
        param_tmp = param_tmp.interpolate(axis=1)
        param= pd.concat([param_tmp, param["unit"]], ignore_index=False, axis=1)

    # correct units to MW and ZAR
    param_yr = param.columns.drop("unit")

    param = convert_cost_units(param, snakemake.config["costs"]["USD_to_ZAR"], snakemake.config["costs"]["EUR_to_ZAR"])
    
    full_param = pd.DataFrame(
        index = pd.MultiIndex.from_product(
            [
                param.index.get_level_values(0).unique(),
                param.index.get_level_values(1).unique()]),
        columns = param.columns
    )
    full_param.loc[param.index] = param.values

    # full_costs adds default values when missing from costs table
    config_defaults = snakemake.config["electricity"]["extendable_parameters"]["defaults"]
    for default in param.index.get_level_values(0).intersection(config_defaults.keys()):
        full_param.loc[param.loc[(default, slice(None)),:].index, :] = param.loc[(default, slice(None)),:]
        full_param.loc[(default, slice(None)), param_yr] = full_param.loc[(default, slice(None)), param_yr].fillna(config_defaults[default])
    #full_param = full_param.fillna("default")
    param = full_param.copy()

    # Get entries where FOM is specified as % of CAPEX
    fom_perc_capex=param.loc[param.unit.str.contains(r"\%capex/year") == True, param_yr]
    fom_perc_capex_idx=fom_perc_capex.index.get_level_values(1)

    add_param = pd.DataFrame(
        index = pd.MultiIndex.from_product([["capital_cost","marginal_cost"],param.loc["FOM"].index]),
        columns = param.columns
    )
    
    param = pd.concat([param, add_param],axis=0)
    param.loc[("FOM",fom_perc_capex_idx), param_yr] *= (param.loc[("investment", fom_perc_capex_idx),param_yr]).values/100.0
    param.loc[("FOM",fom_perc_capex_idx), "unit"] = param.loc[("investment", fom_perc_capex_idx),"unit"].values

    capital_costs = annualise_costs(
        param.loc["investment", param_yr],
        param.loc["lifetime", param_yr], 
        param.loc["discount_rate", param_yr],
        param.loc["FOM", param_yr],
    )

    param.loc["capital_cost", param_yr] = capital_costs.fillna(0).values
    param.loc["capital_cost","unit"] = "R/MWe"

    vom = param.loc["VOM", param_yr].fillna(0)
    fuel = (param.loc["fuel", param_yr] / param.loc["efficiency", param_yr]).fillna(0)

    param.loc[("marginal_cost", vom.index), param_yr] = vom.values
    param.loc[("marginal_cost", fuel.index), param_yr] += fuel.values
    param.loc["marginal_cost","unit"] = "R/MWhe"

    #max_hours = snakemake.config["electricity"]["max_hours"]
    #param.loc[("capital_cost","battery"), :] = param.loc[("capital_cost","battery inverter"),:]
    #param.loc[("capital_cost","battery"), param_yr] += max_hours["battery"]*param.loc[("capital_cost", "battery storage"), param_yr]
    
    return param

# def update_transmission_costs(n, costs, length_factor=1.0, simple_hvdc_costs=False):
#     # Currently only average transmission costs are implemented
#     n.lines["capital_cost"] = (
#         n.lines["length"] * length_factor * costs.loc[("capital_cost","HVAC_overhead"), costs.columns.drop("unit")].mean()
#     )

#     if n.links.empty:
#         return

#     dc_b = n.links.carrier == "DC"
#     # If there are no "DC" links, then the "underwater_fraction" column
#     # may be missing. Therefore we have to return here.
#     # TODO: Require fix
#     if n.links.loc[n.links.carrier == "DC"].empty:
#         return

#     if simple_hvdc_costs:
#         hvdc_costs = (
#             n.links.loc[dc_b, "length"]
#             * length_factor
#             * costs.loc[("capital_cost","HVDC_overhead"),:].mean()
#         )
#     else:
#         hvdc_costs = (
#             n.links.loc[dc_b, "length"]
#             * length_factor
#             * (
#                 (1.0 - n.links.loc[dc_b, "underwater_fraction"])
#                 * costs.loc[("capital_cost","HVDC_overhead"),:].mean()
#                 + n.links.loc[dc_b, "underwater_fraction"]
#                 * costs.loc[("capital_cost","HVDC_submarine"),:].mean()
#             )
#             + costs.loc[("capital_cost","HVDC inverter_pair"),:].mean()
#         )
#     n.links.loc[dc_b, "capital_cost"] = hvdc_costs


"""
********************************************************************************
    Add load to the network 
********************************************************************************
"""

def attach_load(n, scenario_setup):
    """
    Attaches load to the network based on the provided annual demand.
    Demand is disaggregated by the load_disaggreate function according to either population 
    or GVA (GDP) in each region. 

    Args:
    - n: The network object.
    - annual_demand: A DataFrame containing the annual demand values.

        """

    load = pd.read_csv(snakemake.input.load,index_col=[0],parse_dates=True)

    annual_load = (
        pd.read_excel(
            os.path.join(
                scenario_setup["sub_path"],
                "annual_load.xlsx",
            ),
            sheet_name="annual_load",
            index_col=[0],
        )
    ).loc[scenario_setup["load_trajectory"]]

    annual_load = annual_load.drop(["unit","Source"]).T*1e6
    profile_load = normed(remove_leap_day(load.loc[str(snakemake.config["years"]["reference_load_year"]),"system_energy"]))
    
    if n.multi_invest:
        load=pd.Series(0,index=n.snapshots)
        for y in n.investment_periods:
            load.loc[y]=profile_load.values*annual_load.loc[y]
    else:
        load = pd.Series(profile_load.values*annual_load[n.snapshots[0].year], index = n.snapshots)

    if len(n.buses) == 1:
        n.add("Load", "RSA",
            bus="RSA",
            p_set=load)
    else:
        n.madd("Load", list(n.buses.index),
            bus = list(n.buses.index),
            p_set = load_disaggregate(load, normed(n.buses[snakemake.config["electricity"]["load_disaggregation"]])))


"""
********************************************************************************
    Function to define p_max_pu and p_min_pu profiles 
********************************************************************************
"""
def init_pu_profiles(gens, snapshots):
    pu_profiles = pd.DataFrame(
        index = pd.MultiIndex.from_product(
            [["max", "min"], snapshots], 
            names=["profile", "snapshots"]
            ), 
        columns = gens.index
    )
    pu_profiles.loc["max"] = 1 
    pu_profiles.loc["min"] = 0

    return pu_profiles


def extend_reference_data(n, ref_data, snapshots):

    # delete data from years if all zeros
    ref_data = ref_data.groupby(pd.Grouper(freq='Y')).filter(lambda x: not (x==0).all().all())

    ext_years = snapshots.year.unique()
    if len(ref_data.shape) > 1:
        extended_data = pd.DataFrame(0, index=snapshots, columns=ref_data.columns)
    else:
        extended_data = pd.Series(0, index=snapshots)     
    ref_years = ref_data.index.year.unique()

    for _ in range(int(np.ceil(len(ext_years) / len(ref_years)))-1):
        ref_data = pd.concat([ref_data, ref_data],axis=0)

    extended_data.iloc[:] = ref_data.iloc[range(len(extended_data))].values

    return extended_data.clip(lower=0., upper=1.)


def get_eaf_profiles(snapshots, type):
      
    outages = pd.read_excel(
            os.path.join(scenario_setup["sub_path"], "plant_availability.xlsx"), 
            sheet_name='outage_profiles',
            index_col=[0,1,2],
            header=[0,1],
    ).loc[scenario_setup["outage_profiles"]]
    outages = outages[type+"_generators"]

    def proc_outage(outages, _type, snapshots):
        out_df = outages.loc[(_type, range(1,54)), :]
        out_df.index = range(1,54)
        std_dev = outages.loc[(_type, "std_dev_noise"),:]

        week_idx = snapshots.isocalendar().week      # pandas ≥ 1.1, still valid in 2.x
        eaf_hrly = out_df.loc[week_idx]
        #eaf_hrly = out_df.loc[snapshots.week]
        eaf_hrly.index = snapshots

        for col in eaf_hrly.columns:
            eaf_hrly[col] = add_noise(eaf_hrly[col], std_dev[col], 48)

        return eaf_hrly

    pclf = proc_outage(outages, "planned", snapshots)
    uoclf = proc_outage(outages, "unplanned", snapshots)

    
    return 1- (pclf + uoclf)

def clip_pu_profiles(n, pu, gen_list, lower=0, upper=1):
    n.generators_t[pu] = n.generators_t[pu].copy()
    n.generators_t[pu].loc[:, gen_list] = get_as_dense(n, "Generator", pu)[gen_list].clip(lower=lower, upper=upper)


def proj_eaf_override(eaf_hrly, snapshots, include = "_EAF", exclude = "extendable"):
    """
    Overrides the hourly EAF (Energy Availability Factor) values with projected EAF values, if these are defined
    under the project_parameters tab in the model_file.xlsx. Existing generators have suffix _EAF and extendable generators
    have the suffix _extendable_EAF by convention.  

    Args:
    - eaf_hrly: A DataFrame containing the hourly EAF values.
    - projections: A DataFrame containing the projected EAF values.
    - snapshots: A Series containing the snapshots.
    - include: A string used to filter the projections based on the index.
    - exclude: A string used to exclude certain projections based on the index.

    Relevant model_file.xlsx settings:
        project_parameters: parameters with _EAF or _extendable_EAF suffix  
    
    """
    annual_avail = pd.read_excel(
            os.path.join(scenario_setup["sub_path"], "plant_availability.xlsx"), 
            sheet_name='annual_availability',
            index_col=[0,1],
    ).loc[scenario_setup["annual_availability"]]

    eaf_yrly = eaf_hrly.groupby(eaf_hrly.index.year).mean()
    proj_eaf = annual_avail.loc[(annual_avail.index.str.contains(include) & ~annual_avail.index.str.contains(exclude)), snapshots.year.unique()]
    proj_eaf.index = proj_eaf.index.str.replace(include,"")

    # remove decom_stations
    proj_eaf = proj_eaf[proj_eaf.index.isin(eaf_yrly.columns)]
    scaling = proj_eaf.T.div(eaf_yrly[proj_eaf.index], axis="columns", level="year").fillna(1)

    for y in snapshots.year.unique():
        eaf_hrly.loc[str(y), scaling.columns] *= scaling.loc[y, :]  

    return eaf_hrly

def generate_eskom_re_profiles(n, carriers):
    """
    Generates Eskom renewable energy profiles for the network, based on the Eskom Data Portal information, found under
    https://www.eskom.co.za/dataportal/. Data is available from 2018 to 2023 for aggregate carriers (e.g. all solar_pv, biomass, hydro etc).
    The user can specify whether to use this data under config.yaml. These Eskom profiles for biomass, hydro and hydro_import are used by default.

    Args:
    - n: The PyPSA network object.

    Relevant config.yaml settings:
    electricity:
        renewable_generators:
            carriers:
    years:
        reference_weather_years:
    enable:
        use_eskom_wind_solar
    """
    ext_years = get_investment_periods(n.snapshots, n.multi_invest)
    ref_years = snakemake.config["years"]["reference_weather_years"]
    carriers = [elem for elem in carriers if not elem.startswith(("wind","solar_pv"))]

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
def generate_fixed_wind_solar_profiles(n, gens, snapshots, carriers, pu_profiles):
    """
    Generates fixed wind and solar PV profiles for the network based on timeseries data that is pre-calculated
    and using the notebooks under resource_processing. Data is loaded from NetCDF format.


    Args:
    - n: The network object.
    - gens: A DataFrame containing the generator information.
    - snapshots: A Series containing the snapshots.
    - pu_profiles: The DataFrame to store the pu profiles.

    """

    #only select entries in carriers that start with wind or solar_pv
    carriers = [elem for elem in carriers if elem.startswith(("wind","solar_pv"))]
    config = snakemake.config["electricity"]["renewable_generators"]
    ref_years = snakemake.config["years"]["reference_weather_years"]
    for carrier in carriers:
        if carrier not in ["solar_pv_rooftop", "wind_offshore"]:
            base_carrier = "wind" if carrier.startswith("wind") else "solar_pv"
        else:
            base_carrier = carrier
            
        data = f"{carrier}_fixed_{config['resource_profiles']['datasets'][carrier]}"
        pu= xr.open_dataarray(snakemake.input.renewable_profiles, group=data).sel(param="pu").to_pandas()
        pu = remove_leap_day(pu)
        pu = pu[pu.index.year.isin(ref_years[base_carrier])]
        mapping = gens.loc[(gens["Model Key"] != np.nan) & (gens["carrier"] == carrier),"Model Key"]
        mapping = pd.Series(mapping.index,index=mapping.values)
        pu = pu[mapping.index]
        pu.columns = mapping[pu.columns].values
        pu = extend_reference_data(n, pu, snapshots) * (1-config["degradation_adj_capacity_factor"][carrier])
        pu_profiles.loc["max", pu.columns] = pu.values
        pu_profiles.loc["min", pu.columns] = 0.98*pu.values # Existing REIPPP take or pay constraint (100% can cause instabilitites)

    return pu_profiles


def generate_extendable_wind_solar_profiles(n, gens, snapshots, carriers, pu_profiles):
    carriers = [elem for elem in carriers if elem.startswith(("wind","solar_pv"))]
    gens = gens.query("carrier == @carriers & p_nom_extendable")

    years = snapshots.year.unique()
    config_datasets = snakemake.config["electricity"]["renewable_generators"]["resource_profiles"]["datasets"]
    config = snakemake.config["electricity"]["renewable_generators"]

    def get_base_carrier(carrier):
        return "wind" if carrier.startswith("wind") else "solar_pv"

    for carrier in carriers:
        base_carrier = carrier if carrier in ["solar_pv_rooftop", "wind_offshore"] else get_base_carrier(carrier)
        if len(n.buses) == 1:
            config_profiles = config["resource_profiles"]["single_node_profiles"]
            dataset = f"{base_carrier}_{config_profiles[carrier][0]}_{config_datasets[base_carrier]}"
            bus_ref = config_profiles[carrier][1]
            weights = config_profiles[carrier][2]/np.array(config_profiles[carrier][2]).sum()
            if carrier == "wind_offshore":
                data = xr.open_dataarray(snakemake.input.renewable_profiles, group=dataset).sel(bus=bus_ref, _type="floating", quantile=0.9).to_pandas()
            else:
                data = xr.open_dataarray(snakemake.input.renewable_profiles, group=dataset).sel(bus=bus_ref, intra_region=scenario_setup["resource_area"]).to_pandas()
        else:
            dataset = f"{base_carrier}_{len(n.buses)}_{config_datasets[base_carrier]}"
            data = xr.open_dataarray(snakemake.input.renewable_profiles, group=dataset)

        for bus in n.buses.index:
            pu_ref = data.sel(bus=bus, intra_region=scenario_setup["resource_area"]).to_pandas() if len(n.buses)!=1 else data.dot(weights)                
            pu_ref = pu_ref[pu_ref.index.year.isin(snakemake.config["years"]["reference_weather_years"][base_carrier])]
            pu_ref = remove_leap_day(pu_ref)
            pu = pu_ref.copy()
            for y in years:
                pu.name = f"{bus}-{carrier}-{y}"
                pu_profiles.loc["max", pu.name] = extend_reference_data(n, pu, snapshots).values * (1 - config["degradation_adj_capacity_factor"][carrier])

    return pu_profiles

def generate_rmippp_profiles(gens, pu_profiles):
    gen_list = gens[gens.carrier=="rmippp"].index
    pu_profiles.loc[("max", slice(None)), gen_list] = 1
    pu_profiles.loc[("max", pu_profiles.index.get_level_values(1).hour<5), gen_list] = 0
    pu_profiles.loc[("max", pu_profiles.index.get_level_values(1).hour>21), gen_list] = 0
    return pu_profiles

def group_pu_profiles(pu_profiles, component_df):
    years = pu_profiles.index.get_level_values(1).year.unique()
    p_nom_pu = pd.DataFrame(1, index = pu_profiles.loc["max"].index, columns = [])
    pu_mul_p_nom = pu_profiles * component_df["p_nom"]

    filtered_df = component_df[component_df["apply_grouping"]].copy().fillna(0)

    for bus in filtered_df.bus.unique():
        for carrier in filtered_df.carrier.unique():
            carrier_list = filtered_df[(filtered_df["carrier"] == carrier) & (filtered_df["bus"] == bus)].index

            for y in years:
                active = carrier_list[(component_df.loc[carrier_list, ["build_year", "lifetime"]].sum(axis=1) >= y) & (component_df.loc[carrier_list, "build_year"] <= y)]
                if len(active)>0:
                    key_list = filtered_df.loc[active, "Grouping"]
                    for key in key_list.unique():
                        active_key = active[filtered_df.loc[active, "Grouping"] == key]
                        init_active_key = carrier_list[filtered_df.loc[carrier_list, "Grouping"] == key]
                        pu_profiles.loc[(slice(None), str(y)), bus + "-" + carrier + "_" + key] = pu_mul_p_nom.loc[(slice(None), str(y)), active_key].sum(axis=1) / component_df.loc[init_active_key, "p_nom"].sum()
                        p_nom_pu.loc[str(y), bus + "-" + carrier + "_" + key] = component_df.loc[active_key, "p_nom"].sum() / component_df.loc[init_active_key, "p_nom"].sum()
            pu_profiles.drop(columns = carrier_list, inplace=True)

    return pu_profiles.fillna(0), p_nom_pu.fillna(0) # TODO check .fillna(0) doesn't make ramp_rate infeasible on p_max_pu

"""
********************************************************************************
    Functions to define and attach generators to the network  
********************************************************************************
"""
def load_fixed_components(carriers, start_year, config, tech_flag):
    """
    Load components from a model file based on specified filters and configurations.

    Args:
        model_file: The file path to the model file.
        scenario_setup: The model setup object.
        carriers: A list of carriers to filter the generators.
        start_year: The start year for the components.
        config: A dictionary containing configuration settings.

    Returns:
        A DataFrame containing the loaded components.
    """

    component_file = os.path.join(scenario_setup["sub_path"], "fixed_technologies.xlsx")
    if tech_flag == "Generator":
        conv_tech = read_and_filter_generators(component_file, "conventional", scenario_setup["fixed_conventional"], carriers)
        re_tech = read_and_filter_generators(component_file, "renewables", scenario_setup["fixed_renewables"], carriers)

        conv_tech["apply_grouping"] = config["conventional_generators"]["apply_grouping"]
        conv_tech["Type"], conv_tech["Status"] = "Generator", "fixed"

        re_tech["apply_grouping"] = config["renewable_generators"]["apply_grouping"]
        re_tech.set_index((re_tech["Model Key"] + "_" + re_tech["Carrier"]).values,inplace=True)
        re_tech["Type"], re_tech["Status"] = "Generator", "fixed"

        tech= pd.concat([conv_tech, re_tech])

    else:
        tech = read_and_filter_generators(component_file, "storage", scenario_setup["fixed_storage"], carriers)
        tech["apply_grouping"] = config["storage"]["apply_grouping"]
        tech["Type"], tech["Status"] = "StorageUnit", "fixed"

    tech = map_component_parameters(tech, start_year,tech_flag)
    tech = tech.query("(p_nom > 0) & x.notnull() & y.notnull() & (lifetime >= 0)")
    
    return tech

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

    regions_gdf = gpd.read_file(regions).to_crs(snakemake.config["gis"]["crs"]["distance_crs"]).set_index("name")
    gps_gdf = gpd.GeoDataFrame(
        geometry=gpd.GeoSeries([Point(o.x, o.y) for o in component_df[["x", "y"]].itertuples()],
        index=component_df.index, 
        crs=crs_config["geo_crs"]
    ).to_crs(crs_config["distance_crs"]))
    joined = gpd.sjoin(gps_gdf, regions_gdf, how="left", predicate="within")
    component_df["bus"] = joined.name.copy()

    if empty_bus := list(component_df[~component_df["bus"].notnull()].index):
        logger.warning(f"Dropping generators/storage units with no bus assignment {empty_bus}")
        component_df = component_df[component_df["bus"].notnull()]

    return component_df


def group_components(component_df, attrs):
    """
    Apply grouping of similar carrier if specified in snakemake config.

    Args:
        component_df: A DataFrame containing generator/storage_unit data.

    Returns:
        A tuple containing two DataFrames: grouped_df, non_grouped_df
    """
    
    params = ["bus", "carrier", "lifetime", "build_year", "p_nom", "efficiency", "ramp_limit_up", "ramp_limit_down", "marginal_cost", "capital_cost"]
    uc_params = ["ramp_limit_start_up","ramp_limit_shut_down", "start_up_cost", "shut_down_cost", "min_up_time", "min_down_time"] #,"p_min_pu"]
    params += uc_params    
    param_cols = [p for p in params if p not in ["bus","carrier","p_nom"]]

    filtered_df = component_df.query("apply_grouping").copy().fillna(0)#[component_df["apply_grouping"]].copy().fillna(0)

    if len(filtered_df) > 0:
        grouped_df = pd.DataFrame(index=filtered_df.groupby(["Grouping", "carrier", "bus"]).sum().index, columns = param_cols)
        grouped_df["p_nom"] = filtered_df.groupby(["Grouping", "carrier", "bus"]).sum()["p_nom"]
        grouped_df["build_year"] = filtered_df.groupby(["Grouping", "carrier", "bus"]).min()["build_year"]
        grouped_df["lifetime"] = filtered_df.groupby(["Grouping", "carrier", "bus"]).max()["lifetime"]
        
        # calculate weighted average of remaining parameters in gens dataframe
        for param in [p for p in params if p not in ["bus","carrier","p_nom", "lifetime", "build_year"]]:
            weighted_sum = filtered_df.groupby(["Grouping", "carrier", "bus"]).apply(lambda x: (x[param] * x["p_nom"]).sum())
            total_p_nom = filtered_df.groupby(["Grouping", "carrier", "bus"])["p_nom"].sum()
            weighted_average = weighted_sum / total_p_nom 
            grouped_df.loc[weighted_average.index, param] = weighted_average.values
        
        rename_idx = grouped_df.index.get_level_values(2) +  "-" + grouped_df.index.get_level_values(1) +  "_" + grouped_df.index.get_level_values(0)
        grouped_df = grouped_df.reset_index(level=[1,2]).replace(0, np.nan).set_index(rename_idx) 
    else:
        grouped_df = pd.DataFrame(columns=params)
    
    non_grouped_df = component_df[~component_df["apply_grouping"]][params].copy()
    # Fill missing values with default values (excluding defaults that are NaN)    
    grouped_df = apply_default_attr(grouped_df, attrs)
    non_grouped_df = apply_default_attr(non_grouped_df, attrs)

    return grouped_df, non_grouped_df

def attach_fixed_generators(n, carriers):

    # setup carrier info
    gen_attrs = n.component_attrs["Generator"]
    conv_carriers = carriers["fixed"]["conventional"]
    re_carriers = carriers["fixed"]["renewables"]
    carriers = conv_carriers + re_carriers

    start_year = get_start_year(n.snapshots, n.multi_invest)
    snapshots = get_snapshots(n.snapshots, n.multi_invest)
    
    # load generators from model file
    gens = load_fixed_components(carriers, start_year, snakemake.config["electricity"], "Generator")
    gens = map_components_to_buses(gens, snakemake.input.supply_regions, snakemake.config["gis"]["crs"])
    pu_profiles = init_pu_profiles(gens, snapshots)

    unique_entries = set()
    coal_gens =  [unique_entries.add(g.split("*")[0]) or g.split("*")[0] for g in gens[gens.carrier == 'coal'].index if g.split("*")[0] not in unique_entries]
    conv_pu = get_eaf_profiles(snapshots, "fixed")
    
    not_in_pu = [g for g in coal_gens if g not in conv_pu.columns]
    conv_pu[not_in_pu] = 1    
    conv_pu = proj_eaf_override(conv_pu, snapshots, include = "_EAF", exclude = "extendable")
    
    # Copy pu_profiles max to conv_pu
    common_gens = [g for g in conv_pu.columns if g in pu_profiles.loc["max"].columns]
    pu_profiles.loc["max", common_gens] = conv_pu[common_gens].values

    split_gens = [g for g in gens.index if "*" in g]
    for gen in split_gens:
        station = gen.split("*")[0]
        pu_profiles.loc["max", gen] = conv_pu[station].values

    # eskom_carriers = [carrier for carrier in conv_carriers if carrier not in ["nuclear", "hydro", "hydro_import"]]
    # for col in gens.query("Grouping == 'eskom' & carrier in @eskom_carriers").index:
    #     pu_profiles.loc["max", col] = conv_pu[col.split("*")[0]].values


    #rmippp_constraints(n, n.snapshots)

    # Hourly data from Eskom data portal
    eskom_re_pu = generate_eskom_re_profiles(n, re_carriers)
    eskom_re_carriers = eskom_re_pu.columns
    for col in gens.query("carrier in @eskom_re_carriers").index:
        pu_profiles.loc["max", col] = eskom_re_pu[gens.loc[col, "carrier"]].values
        pu_profiles.loc["min", col] = eskom_re_pu[gens.loc[col, "carrier"]].values

    # Wind and solar profiles if not using Eskom data portal
    pu_profiles = generate_fixed_wind_solar_profiles(n, gens, snapshots, re_carriers, pu_profiles)

    # Add specific dispatch profile to RMIPPP generators
    pu_profiles = generate_rmippp_profiles(gens, pu_profiles)
    pu_profiles, p_nom_pu = group_pu_profiles(pu_profiles, gens) #includes both grouped an non-grouped generators
    grouped_gens, non_grouped_gens = group_components(gens, gen_attrs)
    grouped_gens["p_nom_extendable"] = False
    non_grouped_gens["p_nom_extendable"] = False
    
    n.import_components_from_dataframe(drop_non_pypsa_attrs(n, "Generator", non_grouped_gens), "Generator")
    n.import_components_from_dataframe(drop_non_pypsa_attrs(n, "Generator", grouped_gens), "Generator")

    pu_max, pu_min = pu_profiles.loc["max"], pu_profiles.loc["min"]
    pu_max.index, pu_min.index, p_nom_pu.index = n.snapshots, n.snapshots, n.snapshots

    n.generators_t.p_nom_pu = p_nom_pu
    n.generators_t.p_max_pu = pu_max.clip(lower=0.0, upper=1.0)
    n.generators_t.p_min_pu = pu_min.clip(lower=0.0, upper=1.0)
    
    #for carrier, value in snakemake.config["electricity"]["min_hourly_station_gen"]["fixed"].items():
    #    clip_pu_profiles(n, "p_min_pu", n.generators.query("carrier == @carrier & p_nom_extendable == False").index, lower=value, upper=1.0)

# def extendable_max_build_per_bus_per_carrier():

#     ext_max_build = (
#         pd.read_excel(
#             model_file, 
#             sheet_name='ext_max_total',
#             index_col=[0,1,2,3,4])
#     ).loc[scenario_setup]
#     ext_max_build.replace("unc", np.inf, inplace=True)

#     return ext_max_build.loc[snakemake.wildcards.regions]

def define_extendable_tech(carriers, years, type_, ext_param):

    ext_max_build = pd.read_excel(
        os.path.join(scenario_setup["sub_path"],"extendable_technologies.xlsx"), 
        sheet_name='max_total_installed',
        index_col= [0,1,3,2,4],
    ).loc[(scenario_setup["extendable_max_total"], scenario_setup["regions"], type_, slice(None)), years]
    
    ext_max_build.replace("unc", np.inf, inplace=True)
    ext_max_build.index = ext_max_build.index.droplevel([0, 1, 2])
    ext_max_build = ext_max_build.loc[~(ext_max_build==0).all(axis=1)]

    # Drop extendable techs that are not defined as eligible carriers in ext_techs tab
    if type_ == "Generator":
        eligible_carriers = carriers['extendable']['conventional'] + carriers['extendable']['renewables']
    elif type_ == "StorageUnit":
        eligible_carriers = carriers['extendable']['storage']
    idx = [(bus, c) for (bus, c) in ext_max_build.index if c in eligible_carriers]
    ext_max_build = ext_max_build.loc[idx]


    carrier_names = ext_max_build.index.get_level_values(1)
    if bad_name := list(carrier_names[carrier_names.str.contains("-")]):
        logger.warning(f"Carrier names in extendable_max_build sheet must not contain the character '-'. The following carriers will be ignored: {bad_name}")
        ext_max_build = ext_max_build[~carrier_names.str.contains("-")]
    
    carrier_names = ext_max_build.index.get_level_values(1)
    if bad_name := list(carrier_names[~carrier_names.isin(ext_param.index.get_level_values(1))]):
        logger.warning(f"Carrier names in extendable_max_build sheet must be in the extendable_paramaters sheet. The following carriers will be ignored: {bad_name}")
        ext_max_build = ext_max_build[carrier_names.isin(ext_param.index.get_level_values(1))]

    return (
        ext_max_build[ext_max_build != 0].stack().index.to_series().apply(lambda x: "-".join([x[0], x[1], str(x[2])]))
    ).values


def attach_extendable_generators(n, carriers):
    gen_attrs = n.component_attrs["Generator"]
    config = snakemake.config["electricity"]
    
    conv_carriers = carriers["extendable"]["conventional"]
    re_carriers = carriers["extendable"]["renewables"]

    ext_years = get_investment_periods(n.snapshots, n.multi_invest)
    snapshots = get_snapshots(n.snapshots, n.multi_invest)

    ext_param = load_extendable_parameters(n, scenario_setup, snakemake)
    ext_gens_list = define_extendable_tech(carriers, ext_years, "Generator", ext_param) 
    gens = set_extendable_params("Generator", ext_gens_list, ext_param)
    gens = set_annual_build_limits(gens, ext_years, "Generator")
     
    pu_profiles = init_pu_profiles(gens, snapshots)
    
    # Monthly average EAF for conventional plants from Eskom
    conv_pu = get_eaf_profiles(snapshots, "extendable")
    conv_pu = proj_eaf_override(conv_pu, snapshots, include = "_extendable_EAF", exclude = "NA")

    for col in gens.query("carrier in @conv_carriers & p_nom_extendable == True").index:
        pu_profiles.loc["max", col] = conv_pu[col.split("-")[1]].values

    # Hourly data from Eskom data portal
    eskom_ref_re_pu = generate_eskom_re_profiles(n, re_carriers)  
    eskom_ref_re_carriers = [carrier for carrier in eskom_ref_re_pu.columns if carrier in n.generators.carrier.unique()]# and carrier not in committable_carriers

    for col in gens.query("carrier in @eskom_ref_re_carriers & p_nom_extendable == True").index:
        pu_profiles.loc["max", col] = eskom_ref_re_pu[gens.loc[col, "carrier"]].values


    pu_profiles = generate_extendable_wind_solar_profiles(n, gens, snapshots, re_carriers, pu_profiles)
    
    gens = drop_non_pypsa_attrs(n, "Generator", gens)
    n.import_components_from_dataframe(gens, "Generator")
    n.generators["plant_name"] = n.generators.index.str.split("*").str[0]

    in_network = [g for g in pu_profiles.columns if g in n.generators.index]

    pu_max, pu_min = pu_profiles.loc["max", in_network], pu_profiles.loc["min", in_network]
    pu_max.index, pu_min.index = n.snapshots, n.snapshots
    n.generators_t.p_max_pu.loc[:, in_network] = pu_max.loc[:, in_network].clip(lower=0.0, upper=1.0)
    n.generators_t.p_min_pu.loc[:, in_network] = pu_min.loc[:, in_network].clip(lower=0.0, upper=1.0)

    #for carrier, value in snakemake.config["electricity"]["min_hourly_station_gen"]["fixed"].items():
    #    clip_pu_profiles(n, "p_min_pu", n.generators.query("carrier == @carrier & p_nom_extendable").index, lower=value, upper=1.0)


def set_annual_build_limits(techs, ext_years, component):
    
    default_lim = {"max":np.inf,"min":0}
    for lim in ['max',"min"]:
        annual_limit = pd.read_excel(
            os.path.join(scenario_setup["sub_path"], "extendable_technologies.xlsx"), 
            sheet_name = f"{lim}_annual_installed",
            index_col = [0,1,2,3,4,5],
        ).loc[(scenario_setup[f"extendable_{lim}_annual"], scenario_setup["regions"]),:].droplevel(3)
        if component in annual_limit.index.get_level_values(1):
            if lim =="max":
                annual_limit.replace('unc', np.inf, inplace=True)

            # get bus and carriers
            bus_list = techs.bus.unique()
            carrier_list = techs.carrier.unique()

            bus_list = [bus for bus in techs.bus.unique() if bus in annual_limit.index.get_level_values(0)]
            carrier_list = [carrier for carrier in techs.carrier.unique() if carrier in annual_limit.index.get_level_values(2)]

            #check intersection between carrier_list and annual_limit_max.index.get_level
            for bus in bus_list:
                limit = annual_limit.loc[bus, component]
                for carrier in carrier_list:
                    for year in ext_years:
                        if (f"{bus}-{carrier}-{year}" in techs.index) and (carrier in limit.index):
                            if (lim=="max") & (limit.loc[carrier, year]==0):
                                techs.drop(f"{bus}-{carrier}-{year}", inplace=True)
                            else:
                                techs.loc[f"{bus}-{carrier}-{year}", f"p_nom_{lim}"] = limit.loc[carrier, year]

            techs[f"p_nom_{lim}"].fillna(default_lim[lim], inplace=True)
    return techs 

def set_extendable_params(c, bus_carrier_years, ext_param, **config):
    
    if len(bus_carrier_years)==0:
        return
    
    default_param = [
        "bus",
        "p_nom_extendable",
        "carrier",
        "build_year",
        "lifetime",
        "capital_cost",
        "marginal_cost",
        "ramp_limit_up",
        "ramp_limit_down",
        "efficiency",
    ]
    uc_param = [
        "ramp_limit_start_up",
        "ramp_limit_shut_down",
        "min_up_time",
        "min_down_time",
        "start_up_cost",
        "shut_down_cost",
    ]


    if c == "StorageUnit":
        default_param += ["max_hours", "efficiency_store", "efficiency_dispatch"]

    default_col = [p for p in default_param if p not in ["bus", "carrier", "build_year", "p_nom_extendable", "efficiency_store", "efficiency_dispatch"]]

    component_df = pd.DataFrame(index = bus_carrier_years, columns = default_param)
    component_df["p_nom_extendable"] = True
    component_df["p_nom"] = 0
    component_df["bus"] = component_df.index.str.split("-").str[0]
    component_df["carrier"] = component_df.index.str.split("-").str[1]
    component_df["build_year"] = component_df.index.str.split("-").str[2].astype(int)
    
    if c == "Generator":
        component_df = pd.concat([component_df, pd.DataFrame(index = bus_carrier_years, columns = uc_param)],axis=1)
        for param in default_col + uc_param:
            component_df[param] =  component_df.apply(lambda row: ext_param.loc[(param, row["carrier"]), row["build_year"]], axis=1)
            component_df = apply_default_attr(component_df, n.component_attrs[c])
    elif c == "StorageUnit":
        for param in default_col:
            component_df[param] =  component_df.apply(lambda row: ext_param.loc[(param, row["carrier"]), row["build_year"]], axis=1)
        
        component_df["cyclic_state_of_charge"] = True
        component_df["cyclic_state_of_charge_per_period"] = True
        component_df["efficiency_store"] = component_df["efficiency"]**0.5
        component_df["efficiency_dispatch"] = component_df["efficiency"]**0.5
        component_df = component_df.drop("efficiency", axis=1)
    
    return component_df


def apply_extendable_phase_in(n):
    param = load_extendable_parameters(n, scenario_setup, snakemake).loc["build_phase_in"]
    pu_max = get_as_dense(n, "Generator", "p_max_pu")
    ext_i = n.generators.query("p_nom_extendable").index
    
    phase_in = pd.DataFrame(1, index =range(8760), columns = ["overnight", "linear", "quarterly"]) 
    phase_in.loc[:, "linear"] = np.round(np.arange(0, 1, 1/8760),3)
    phase_in.loc[:2190, "quarterly"] = 0.25
    phase_in.loc[2190:4380, "quarterly"] = 0.5
    phase_in.loc[4380:6570, "quarterly"] = 0.75

    for gen in ext_i:
        build_year = n.generators.loc[gen, "build_year"]  
        method = param.loc[gen.split('-')[1], build_year]
        phasing = phase_in[method]
        if method not in ["overnight", "linear", "quarterly"]:
            raise ValueError(f"Invalid method for phase-in of extendable generators: {method}. Choose from 'overnight', 'linear' or 'quarterly'.")

        if method != "overnight":
            n.generators.loc[gen, "lifetime"] += 1

        gen_pu_max = pu_max[gen]
        build_year_level = [build_year] * len(gen_pu_max.loc[build_year].index)
        phasing.index = pd.MultiIndex.from_arrays([build_year_level, gen_pu_max.loc[build_year].index])
        
        gen_pu_max.loc[pd.IndexSlice[gen_pu_max.index.get_level_values(0) < build_year]] = 0
        gen_pu_max.loc[build_year] =  gen_pu_max.loc[build_year] * phasing

        n.generators_t.p_max_pu.loc[:, gen] = gen_pu_max.values


def adjust_for_variable_fuel_costs(n):
    param = load_extendable_parameters(n, scenario_setup, snakemake)
    # check if any variation in marginal cost across years
    variable_fuel_cost = param.loc["fuel", n.investment_periods]
    variable_fuel_cost = variable_fuel_cost[variable_fuel_cost.mean(axis=1) != variable_fuel_cost.iloc[:, 0]]

    gen_list = n.generators.query("carrier in @variable_fuel_cost.index").index
    marginal_cost = pd.DataFrame(index = n.snapshots, columns = gen_list)
    for carrier in variable_fuel_cost.index:
        for gen in n.generators.query("carrier == @carrier").index:
            cost = variable_fuel_cost.loc[carrier] / n.generators.loc[gen, "efficiency"] + param.loc[("VOM", carrier), n.investment_periods]
            for y in n.investment_periods:
                marginal_cost.loc[y, gen_list] = cost.loc[y]
            n.generators.marginal_cost[gen] = 0
            n.generators_t.marginal_cost[gen] = marginal_cost[gen]

def set_hourly_coal_generation_threshold(n):
    if scenario_setup["min_station_hourly"] in ["NA", "None", "none"]:
        return
    
    min_pu_override = (
        pd.read_excel(
            os.path.join(scenario_setup["sub_path"],"plant_availability.xlsx"), 
            sheet_name="min_station_hrly_cap_fact",
            index_col=[0,1])
            .loc[scenario_setup["min_station_hourly"]]
    )
    carrier_list = list(min_pu_override.index)

    n.generators_t.p_min_pu = n.generators_t.p_min_pu.copy()    
    for carrier in carrier_list:
        gen_list = n.generators.query("carrier == @carrier").index
        for y in n.investment_periods:
            n.generators_t.p_min_pu.loc[y, gen_list] = get_as_dense(n, "Generator", "p_min_pu")[gen_list].clip(lower=min_pu_override.loc[carrier, y])

"""
********************************************************************************
    Functions to define and attach storage units to the network  
********************************************************************************
"""
def attach_fixed_storage(n, carriers): 
    carriers = carriers["fixed"]["storage"]
    start_year = get_start_year(n.snapshots, n.multi_invest)
    
    storage = load_fixed_components(carriers, start_year, snakemake.config["electricity"], "StorageUnit")
    storage = map_components_to_buses(storage, snakemake.input.supply_regions, snakemake.config["gis"]["crs"])

    storage["efficiency_store"] = storage["efficiency"]**0.5
    storage["efficiency_dispatch"] = storage["efficiency"]**0.5
    storage["cyclic_state_of_charge"], storage["p_nom_extendable"] = True, False
    storage["p_min_pu"] = -1

    storage = drop_non_pypsa_attrs(n, "StorageUnit", storage)
    n.import_components_from_dataframe(storage, "StorageUnit")

def attach_extendable_storage(n, carriers):
    st_carriers = carriers["extendable"]["storage"]
    config = snakemake.config["electricity"]
    ext_years = get_investment_periods(n.snapshots, n.multi_invest)
    ext_param = load_extendable_parameters(n, scenario_setup, snakemake)
    ext_storage_list = define_extendable_tech(carriers, ext_years, "StorageUnit", ext_param)
    storage = set_extendable_params("StorageUnit", ext_storage_list, ext_param, **config)
    storage = set_annual_build_limits(storage, ext_years, "StorageUnit")
    if len(ext_storage_list):
        storage = drop_non_pypsa_attrs(n, "StorageUnit", storage)
        n.import_components_from_dataframe(storage, "StorageUnit")

"""
********************************************************************************
    Transmission network functions  
********************************************************************************
"""

# def convert_lines_to_links(n):
#     """
#     Convert AC lines to DC links for multi-decade optimisation with line
#     expansion.

#     Losses of DC links are assumed to be 3% per 1000km
#     """
#     years = get_investment_periods(n.snapshots, n.multi_invest)

#     logger.info("Convert AC lines to DC links to perform multi-decade optimisation.")
#     extendable = False
#     p_nom = n.lines.s_nom

#     for y in years:
#         n.madd(
#             "Link",
#             n.lines.index + "_" + str(y),
#             bus0 = n.lines.bus0,
#             bus1 = n.lines.bus1,
#             p_nom = p_nom,
#             p_nom_min = n.lines.s_nom,
#             p_min_pu = -1,
#             lifetime = 100,
#             efficiency = 1 - 0.03 * n.lines.length / 1000,
#             marginal_cost = 0,
#             length = n.lines.length,
#             capital_cost = n.lines.capital_cost,
#             p_nom_extendable = extendable,
#         )
#         if y == years[0]:
#             extendable = True
#             p_nom = 0

#     # Remove AC lines
#     logger.info("Removing AC lines")
#     lines_rm = n.lines.index
#     n.mremove("Line", lines_rm)



"""
********************************************************************************
    Other functions
********************************************************************************
"""


def check_pu_profiles(clean_flag):
    p_max_pu = get_as_dense(n, "Generator", "p_max_pu")
    p_min_pu = get_as_dense(n, "Generator", "p_min_pu")

    errors = p_max_pu < p_min_pu
    error_lst = errors.any()
    if errors.any().any() and not clean_flag:
        raise ValueError(
            f'Some generators have p_max_pu < p_min_pu in some hours. This will cause the problem to be infeasible.\nEither set clean_pu_profiles under config to True or correct input assumptions. Errors can be found in the follwing generators:\n{error_lst[error_lst].index}'
        )
    elif errors.any().any() and clean_flag:
        logging.info(f"Adjusting by p_max_pu for {error_lst[error_lst].index}")
        n.generators_t.p_max_pu = p_max_pu.where(~errors, p_min_pu)


def add_carrier_emissions(n):

    emissions = (
        pd.read_excel(
            model_file, 
            sheet_name='emissions',
            index_col=[0,1])
    )
    co2_emissions = emissions.loc["co2_emissions"]
    
    n.madd(
        "Carrier", 
        co2_emissions.index,
        co2_emissions = co2_emissions["Value"],
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


def add_load_shedding(n, cost):
    n.add("Carrier", "load_shedding")
    buses_i = n.buses.index
    n.madd(
        "Generator",
        buses_i,
        "_load_shedding",
        bus = buses_i,
        p_nom = 1e6,  # MW
        carrier = "load_shedding",
        build_year = get_start_year(n.snapshots, n.multi_invest),
        lifetime = 100,
        marginal_cost = cost,
    )


"""
********************************************************************************
        MAIN  
********************************************************************************
"""


if __name__ == "__main__":
    if "snakemake" not in globals():
        from _helpers import mock_snakemake
        snakemake = mock_snakemake(
            "add_electricity", 
            **{
                "model_type":"capacity",
                "scenario":"IRP_REF_CI",
            }
        )

    scenario_path = snakemake.output[0].split("capacity")[0]
    if not os.path.exists(scenario_path):
        os.makedirs(scenario_path)

    #configure_logging(snakemake, skip_handlers=False)
    logging.basicConfig(
        level=logging.INFO,
        format='%(levelname)s: %(asctime)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    scenario_setup = load_scenario_definition(snakemake)

    logging.info("Loading carriers from scenario file")
    carriers = get_carriers_from_model_file(scenario_setup)

    logging.info(f"Loading base network {snakemake.input.base_network}")
    n = pypsa.Network(snakemake.input.base_network)

    logging.info("Attaching load")
    attach_load(n, scenario_setup)

    logging.info("Attaching fixed generators")
    attach_fixed_generators(n, carriers)

    logging.info("Attaching extendable generators")
    attach_extendable_generators(n, carriers)

    logging.info("Attaching fixed storage")
    attach_fixed_storage(n, carriers)

    logging.info("Attaching extendable storage")
    attach_extendable_storage(n, carriers)

    logging.info("Adjusting for changes in fuel price over time")
    adjust_for_variable_fuel_costs(n)

    adj_by_pu = snakemake.config["electricity"]["adjust_by_p_max_pu"]
    logging.info(f"Adjusting by p_max_pu for {list(adj_by_pu.keys())}")
    adjust_by_p_max_pu(n, adj_by_pu)

    logging.info("Applying coal minimum generation threshold")
    set_hourly_coal_generation_threshold(n)

    logging.info("Applying phase in of extendable generators")
    apply_extendable_phase_in(n)
    
    logging.info("Checking pu_profiles for infeasibilities")
    check_pu_profiles(snakemake.config["electricity"]["clean_pu_profiles"])

    if snakemake.config["solving"]["options"]["load_shedding"]:
        ls_cost = snakemake.config["costs"]["load_shedding"]
        logging.info("Adding load shedding")
        add_load_shedding(n, ls_cost) 

    add_missing_carriers(n)

    logging.info("Exporting network.")
    if n.multi_invest:
        initial_ramp_rate_fix(n)

    if snakemake.wildcards.model_type == "dispatch":
        for y in n.investment_periods:
            sns = n.snapshots[n.snapshots.get_level_values("period")==y]
            n_y = single_year_network_copy(n, snapshots=sns, investment_periods=sns.unique("period"))
            n_y.export_to_netcdf(f"{scenario_path}/dispatch_{y}.nc") 
    else:
        n.generators_t.p_max_pu = n.get_switchable_as_dense("Generator", "p_max_pu").astype(float)
        n.export_to_netcdf(snakemake.output[0])
