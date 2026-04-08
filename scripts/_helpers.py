# SPDX-FileCopyrightText:  PyPSA-RSA, PyPSA-ZA, PyPSA-Earth and PyPSA-Eur Authors
# # SPDX-License-Identifier: MIT
# -*- coding: utf-8 -*-

import os
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

from pypsa.descriptors import get_switchable_as_dense as get_as_dense
from pypsa.descriptors import get_activity_mask, get_active_assets
from pypsa.io import import_components_from_dataframe

import socket

import xarray as xr
"""
List of general helper functions
- configure_logging ->
- normed ->
"""
def configure_logging(snakemake, skip_handlers=False):
    """
    Configure the basic behaviour for the logging module.

    Note: Must only be called once from the __main__ section of a script.
if not os.path.exists(folder_name):
    # If it doesn't exist, create it
    os.makedirs(folder_name)
    print(f"Folder '{folder_name}' was created.")
else:
    print(f"Folder '{folder_name}' already exists.")lse (default)
        Do (not) skip the default handlers created for redirecting output to STDERR and file.
    """

    import logging

    kwargs = snakemake.config.get("logging", dict())
    kwargs.setdefault("level", "INFO")

    if skip_handlers is False:
        fallback_path = Path(__file__).parent.joinpath(
            "..", "logs", f"{snakemake.rule}.log"
        )
        logfile = snakemake.log.get(
            "python", snakemake.log[0] if snakemake.log else fallback_path
        )
        kwargs.update(
            {
                "handlers": [
                    # Prefer the "python" log, otherwise take the first log for each
                    # Snakemake rule
                    logging.FileHandler(logfile),
                    logging.StreamHandler(),
                ]
            }
        )
    logging.basicConfig(**kwargs)

def normed(s):
    return s / s.sum()


"""
List of cost related functions

"""


def add_missing_carriers(n):
    all_carriers = (
        list(n.generators.carrier.unique()) 
        + list(n.storage_units.carrier.unique()) 
        + list(n.links.carrier.unique())
    )

    missing_carriers = pd.Index(all_carriers).difference(n.carriers.index)
    n.madd("Carrier", missing_carriers)

"""
List of IO functions
    - load_network ->
    - sets_path_to_root -> 
    - read_and_filter_generators -> add_electricity.py
    - read_csv_nafix -> 
    - to_csv_nafix -> 
"""


def sets_path_to_root(root_directory_name):
    """
    Search and sets path to the given root directory (root/path/file).

    Parameters
    ----------
    root_directory_name : str
        Name of the root directory.
    n : int
        Number of folders the function will check upwards/root directed.

    """
    import os

    repo_name = root_directory_name
    n = 8  # check max 8 levels above. Random default.
    n0 = n

    while n >= 0:
        n -= 1
        # if repo_name is current folder name, stop and set path
        if repo_name == os.path.basename(os.path.abspath(".")):
            repo_path = os.getcwd()  # os.getcwd() = current_path
            os.chdir(repo_path)  # change dir_path to repo_path
            print("This is the repository path: ", repo_path)
            print("Had to go %d folder(s) up." % (n0 - 1 - n))
            break
        # if repo_name NOT current folder name for 5 levels then stop
        if n == 0:
            print("Cant find the repo path.")
        # if repo_name NOT current folder name, go one dir higher
        else:
            upper_path = os.path.dirname(os.path.abspath("."))  # name of upper folder
            os.chdir(upper_path)

def read_and_filter_generators(file, sheet, index, filter_carriers):
    df = pd.read_excel(
        file, 
        sheet_name=sheet,
        na_values=["-"],
        index_col=[0,1]
    ).loc[index]
    return df[df["carrier"].isin(filter_carriers)]


def read_csv_nafix(file, **kwargs):
    "Function to open a csv as pandas file and standardize the na value"
    if "keep_default_na" in kwargs:
        del kwargs["keep_default_na"]
    if "na_values" in kwargs:
        del kwargs["na_values"]

    return pd.read_csv(file, **kwargs, keep_default_na=False, na_values=NA_VALUES)


def to_csv_nafix(df, path, **kwargs):
    if "na_rep" in kwargs:
        del kwargs["na_rep"]
    if not df.empty:
        return df.to_csv(path, **kwargs, na_rep=NA_VALUES[0])
    with open(path, "w") as fp:
        pass

def add_row_multi_index_df(df, add_index, level):
    if level == 1:
        idx = pd.MultiIndex.from_product([df.index.get_level_values(0),add_index])
        add_df = pd.DataFrame(index=idx,columns=df.columns)
        df = pd.concat([df,add_df]).sort_index()
        df = df[~df.index.duplicated(keep='first')]
    return df

def load_network(import_name=None, custom_components=None):
    """
    Helper for importing a pypsa.Network with additional custom components.

    Parameters
    ----------
    import_name : str
        As in pypsa.Network(import_name)
    custom_components : dict
        Dictionary listing custom components.
        For using ``snakemake.config["override_components"]``
        in ``config.yaml`` define:

        .. code:: yaml

            override_components:
                ShadowPrice:
                    component: ["shadow_prices","Shadow price for a global constraint.",np.nan]
                    attributes:
                    name: ["string","n/a","n/a","Unique name","Input (required)"]
                    value: ["float","n/a",0.,"shadow value","Output"]

    Returns
    -------
    pypsa.Network
    """
    import pypsa
    from pypsa.descriptors import Dict

    override_components = None
    override_component_attrs = None

    if custom_components is not None:
        override_components = pypsa.components.components.copy()
        override_component_attrs = Dict(
            {k: v.copy() for k, v in pypsa.components.component_attrs.items()}
        )
        for k, v in custom_components.items():
            override_components.loc[k] = v["component"]
            override_component_attrs[k] = pd.DataFrame(
                columns=["type", "unit", "default", "description", "status"]
            )
            for attr, val in v["attributes"].items():
                override_component_attrs[k].loc[attr] = val

    return pypsa.Network(
        import_name=import_name,
        override_components=override_components,
        override_component_attrs=override_component_attrs,
    )

def load_disaggregate(v, h):
    return pd.DataFrame(
        v.values.reshape((-1, 1)) * h.values, index=v.index, columns=h.index
    )

def load_scenario_definition(scenario, config, include_run_only = True):
    
    scenario_folder = config["scenarios"]["input_folder"]

    scenario_setup = load_scenario_setup(
        os.path.join(scenario_folder,config["scenarios"]["working_folder"], config["scenarios"]["setup"]), 
        scenario,
        include_run_only = include_run_only,
    )
    scenario_setup["path"] = scenario_folder
    scenario_setup["sub_path"] = scenario_setup["path"] + "/" + config["scenarios"]["working_folder"] + "/sub_scenarios"

    return scenario_setup.fillna("none")

def load_network_for_plots(fn, model_file, config, model_setup_costs, combine_hydro_ps=True, ):
    import pypsa
    from add_electricity import load_costs, update_transmission_costs

    n = pypsa.Network(fn)

    n.loads["carrier"] = n.loads.bus.map(n.buses.carrier) + " load"
    n.stores["carrier"] = n.stores.bus.map(n.buses.carrier)

    n.links["carrier"] = (
        n.links.bus0.map(n.buses.carrier) + "-" + n.links.bus1.map(n.buses.carrier)
    )
    n.lines["carrier"] = "AC line"
    n.transformers["carrier"] = "AC transformer"

    n.lines["s_nom"] = n.lines["s_nom_min"]
    n.links["p_nom"] = n.links["p_nom_min"]

    if combine_hydro_ps:
        n.storage_units.loc[
            n.storage_units.carrier.isin({"PHS", "hydro"}), "carrier"
        ] = "hydro+PHS"

    # if the carrier was not set on the heat storage units
    # bus_carrier = n.storage_units.bus.map(n.buses.carrier)
    # n.storage_units.loc[bus_carrier == "heat","carrier"] = "water tanks"

    Nyears = n.snapshot_weightings.objective.sum() / 8760.0
    costs = load_costs(model_file,
        model_setup_costs,
        config["costs"],
        config["electricity"],
        n.investment_periods)
    
    update_transmission_costs(n, costs)

    return n


def update_p_nom_max(n):
    # if extendable carriers (solar/onwind/...) have capacity >= 0,
    # e.g. existing assets from the OPSD project are included to the network,
    # the installed capacity might exceed the expansion limit.
    # Hence, we update the assumptions.

    n.generators.p_nom_max = n.generators[["p_nom_min", "p_nom_max"]].max(1)


"""
List of PyPSA network statistics functions

"""

def aggregate_capacity(n):
    capacity=pd.DataFrame(
        np.nan,index=np.append(n.generators.carrier.unique(),n.storage_units.carrier.unique()),
        columns=range(n.investment_periods[0],n.investment_periods[-1]+1)
    )

    carriers=n.generators.carrier.unique()
    carriers = carriers[carriers !='load_shedding']
    for y in n.investment_periods:
        capacity.loc[carriers,y]=n.generators.p_nom_opt[(n.get_active_assets('Generator',y))].groupby(n.generators.carrier).sum()

    carriers=n.storage_units.carrier.unique()
    for y in n.investment_periods:
        capacity.loc[carriers,y]=n.storage_units.p_nom_opt[(n.get_active_assets('StorageUnit',y))].groupby(n.storage_units.carrier).sum()

    capacity.loc['ocgt',:]=capacity.loc['ocgt_gas',:]+capacity.loc['ocgt_diesel',:]

        
    return capacity.interpolate(axis=1)

def aggregate_energy(n):
    
    def aggregate_p(n,y):
        return pd.concat(
            [
                (
                    n.generators_t.p
                    .mul(n.snapshot_weightings['objective'],axis=0)
                    .loc[y].sum()
                    .groupby(n.generators.carrier)
                    .sum()
                ),
                (
                    n.storage_units_t.p_dispatch
                    .mul(n.snapshot_weightings['objective'],axis=0)
                    .loc[y].sum()
                    .groupby(n.storage_units.carrier).sum()
                )
            ]
        )
    energy=pd.DataFrame(
        np.nan,
        index=np.append(n.generators.carrier.unique(),n.storage_units.carrier.unique()),
        columns=range(n.investment_periods[0],n.investment_periods[-1]+1)
    )       

    for y in n.investment_periods:
        energy.loc[:,y]=aggregate_p(n,y)

    return energy.interpolate(axis=1)

def aggregate_p_nom(n):
    return pd.concat(
        [
            n.generators.groupby("carrier").p_nom_opt.sum(),
            n.storage_units.groupby("carrier").p_nom_opt.sum(),
            n.links.groupby("carrier").p_nom_opt.sum(),
            n.loads_t.p.groupby(n.loads.carrier, axis=1).sum().mean(),
        ]
    )


def aggregate_p(n):
    return pd.concat(
        [
            n.generators_t.p.sum().groupby(n.generators.carrier).sum(),
            n.storage_units_t.p.sum().groupby(n.storage_units.carrier).sum(),
            n.stores_t.p.sum().groupby(n.stores.carrier).sum(),
            -n.loads_t.p.sum().groupby(n.loads.carrier).sum(),
        ]
    )


def aggregate_e_nom(n):
    return pd.concat(
        [
            (n.storage_units["p_nom_opt"] * n.storage_units["max_hours"])
            .groupby(n.storage_units["carrier"])
            .sum(),
            n.stores["e_nom_opt"].groupby(n.stores.carrier).sum(),
        ]
    )


def aggregate_p_curtailed(n):
    return pd.concat(
        [
            (
                (
                    n.generators_t.p_max_pu.sum().multiply(n.generators.p_nom_opt)
                    - n.generators_t.p.sum()
                )
                .groupby(n.generators.carrier)
                .sum()
            ),
            (
                (n.storage_units_t.inflow.sum() - n.storage_units_t.p.sum())
                .groupby(n.storage_units.carrier)
                .sum()
            ),
        ]
    )

def aggregate_costs(n):

    components = dict(
        Link=("p_nom_opt", "p0"),
        Generator=("p_nom_opt", "p"),
        StorageUnit=("p_nom_opt", "p"),
        Store=("e_nom_opt", "p"),
        Line=("s_nom_opt", None),
        Transformer=("s_nom_opt", None),
    )

    fixed_cost, variable_cost=pd.DataFrame([]),pd.DataFrame([])
    for c, (p_nom, p_attr) in zip(
        n.iterate_components(components.keys(), skip_empty=False), components.values()
    ):
        if c.df.empty:
            continue
    
        if n._multi_invest:
            active = pd.concat(
                {
                    period: get_active_assets(n, c.name, period)
                    for period in n.snapshots.unique("period")
                },
                axis=1,
            )
        if c.name not in ["Line", "Transformer"]: 
            marginal_costs = (
                    get_as_dense(n, c.name, "marginal_cost", n.snapshots)
                    .mul(n.snapshot_weightings.objective, axis=0)
            )

        fixed_cost_tmp=pd.DataFrame(0,index=n.df(c.name).carrier.unique(),columns=n.investment_periods)
        variable_cost_tmp=pd.DataFrame(0,index=n.df(c.name).carrier.unique(),columns=n.investment_periods)
    
        for y in n.investment_periods:
            fixed_cost_tmp.loc[:,y] = (active[y]*c.df[p_nom]*c.df.capital_cost).groupby(c.df.carrier).sum()

            if p_attr is not None:
                p = c.pnl[p_attr].loc[y]
                if c.name == "StorageUnit":
                    p = p[p>=0]
                    
                variable_cost_tmp.loc[:,y] = (marginal_costs.loc[y]*p).sum().groupby(c.df.carrier).sum()

        fixed_cost = pd.concat([fixed_cost,fixed_cost_tmp])
        variable_cost = pd.concat([variable_cost,variable_cost_tmp])
        
    return fixed_cost, variable_cost



def progress_retrieve(url, file, data=None, disable_progress=False, roundto=1.0):
    """
    Function to download data from a url with a progress bar progress in retrieving data

    Parameters
    ----------
    url : str
        Url to download data from
    file : str
        File where to save the output
    data : dict
        Data for the request (default None), when not none Post method is used
    disable_progress : bool
        When true, no progress bar is shown
    roundto : float
        (default 0) Precision used to report the progress
        e.g. 0.1 stands for 88.1, 10 stands for 90, 80
    """
    import urllib

    from tqdm import tqdm

    pbar = tqdm(total=100, disable=disable_progress)

    def dlProgress(count, blockSize, totalSize, roundto=roundto):
        pbar.n = round(count * blockSize * 100 / totalSize / roundto) * roundto
        pbar.refresh()

    if data is not None:
        data = urllib.parse.urlencode(data).encode()

    urllib.request.urlretrieve(url, file, reporthook=dlProgress, data=data)


def get_aggregation_strategies(aggregation_strategies):
    """
    default aggregation strategies that cannot be defined in .yaml format must be specified within
    the function, otherwise (when defaults are passed in the function's definition) they get lost
    when custom values are specified in the config.
    """
    import numpy as np
    from pypsa.networkclustering import _make_consense

    bus_strategies = dict(country=_make_consense("Bus", "country"))
    bus_strategies.update(aggregation_strategies.get("buses", {}))

    generator_strategies = {"build_year": lambda x: 0, "lifetime": lambda x: np.inf}
    generator_strategies.update(aggregation_strategies.get("generators", {}))

    return bus_strategies, generator_strategies

def mock_snakemake(rulename, **wildcards):
    """
    This function is expected to be executed from the "scripts"-directory of "
    the snakemake project. It returns a snakemake.script.Snakemake object,
    based on the Snakefile.

    If a rule has wildcards, you have to specify them in **wildcards.

    Parameters
    ----------
    rulename: str
        name of the rule for which the snakemake object should be generated
    **wildcards:
        keyword arguments fixing the wildcards. Only necessary if wildcards are
        needed.
    """
    import os

    import snakemake as sm
    #try:
    #    from pypsa.descriptors import Dict
    #except:
    from pypsa.definitions.structures import Dict
    from snakemake.script import Snakemake
    from snakemake.common import SNAKEFILE_CHOICES
    from snakemake.api import Workflow
    from snakemake.settings.types import (
        ConfigSettings,
        DAGSettings,
        ResourceSettings,
        StorageSettings,
        WorkflowSettings,
    )

    script_dir = Path(__file__).parent.resolve()
    assert (
        Path.cwd().resolve() == script_dir
    ), f"mock_snakemake has to be run from the repository scripts directory {script_dir}"
    os.chdir(script_dir.parent)
    for p in SNAKEFILE_CHOICES:
        if os.path.exists(p):
            snakefile = p
            break
    workflow = Workflow(ConfigSettings(configfiles=[]), ResourceSettings(), WorkflowSettings(), StorageSettings(), DAGSettings(rerun_triggers=[]), storage_provider_settings=dict())
    workflow.include(snakefile)
    workflow.global_resources = {}
    try:
        rule = workflow.get_rule(rulename)
    except Exception as exception:
        print(
            exception,
            f"The {rulename} might be a conditional rule in the Snakefile.\n"
            f"Did you enable {rulename} in the config?",
        )
        raise
    dag = sm.dag.DAG(workflow, rules=[rule])
    wc = Dict(wildcards)
    job = sm.jobs.Job(rule, dag, wc)

    def make_accessable(*ios):
        for io in ios:
            for i in range(len(io)):
                io[i] = os.path.abspath(io[i])

    make_accessable(job.input, job.output, job.log)
    snakemake = Snakemake(
        job.input,
        job.output,
        job.params,
        job.wildcards,
        job.threads,
        job.resources,
        job.log,
        job.dag.workflow.config,
        job.rule.name,
        None,
    )
    # create log and output dir if not existent
    for path in list(snakemake.log) + list(snakemake.output):
        Path(path).parent.mkdir(parents=True, exist_ok=True)

    os.chdir(script_dir)
    return snakemake


def n_meta_convert_df_to_dict(obj):
    # Base case: if obj is not a dict, series, or dataframe, return it as it is
    if not isinstance(obj, (dict, pd.Series, pd.DataFrame, xr.DataArray)):
        return obj
    # Recursive case: if obj is a dict, series, or dataframe, create a new dict and apply the function to its values
    else:
        # Create a new dict
        new_dict = {}

        # Convert obj to a dict
        if isinstance(obj, pd.Series):
            obj = obj.to_dict()
            obj = {str(k): v for k, v in obj.items()}

        elif isinstance(obj, pd.DataFrame):
            # If DataFrame has multi-index, combine indices into a single string-based index
            if isinstance(obj.index, pd.MultiIndex):
                df_reset = obj.reset_index()
                combined_index = ['_'.join(map(str, row)) for row in df_reset.values[:, :obj.index.nlevels]]
                df_reset['combined_index'] = combined_index
                # We leave the columns in to make it easier to reconsitute the DataFrame later
                #obj = df_reset.set_index('combined_index').drop(columns=obj.index.names).to_dict(orient="index")
                obj = df_reset.set_index('combined_index').to_dict(orient="index")
            else:
                obj = obj.to_dict(orient="index")
        elif isinstance(obj, xr.DataArray):
            # Convert DataArray to a dictionary
            obj = obj.to_dict()    

        # Convert any timestamp index to a float and add to the new dict
        for key in obj:
            if isinstance(key, pd.Timestamp):
                new_key = key.timestamp()
            else:
                new_key = key
            new_dict[new_key] = n_meta_convert_df_to_dict(obj[key])
            
        return new_dict


def save_to_geojson(df, fn, crs = 'EPSG:4326'):
    if os.path.exists(fn):
        os.unlink(fn)  # remove file if it exists

    # save file if the (Geo)DataFrame is non-empty
    if df.empty:
        # create empty file to avoid issues with snakemake
        with open(fn, "w") as fp:
            pass
    else:
        # save file
        df.to_file(fn, driver="GeoJSON",crs=crs)


def read_geojson(fn):
    # if the file is non-zero, read the geodataframe and return it
    if os.path.getsize(fn) > 0:
        return gpd.read_file(fn)
    else:
        # else return an empty GeoDataFrame
        return gpd.GeoDataFrame(geometry=[])


def convert_cost_units(costs, USD_ZAR, EUR_ZAR):
    costs_yr = costs.columns.drop('units')
    costs.loc[costs.units.str.contains("/kW")==True, costs_yr ] *= 1e3
    costs.loc[costs.units.str.contains("USD")==True, costs_yr ] *= USD_ZAR
    costs.loc[costs.units.str.contains("EUR")==True, costs_yr ] *= EUR_ZAR

    costs.loc[costs.units.str.contains('/kW')==True, 'units'] = costs.loc[costs.units.str.contains('/kW')==True, 'units'].str.replace('/kW', '/MW')
    costs.loc[costs.units.str.contains('USD')==True, 'units'] = costs.loc[costs.units.str.contains('USD')==True, 'units'].str.replace('USD', 'ZAR')
    costs.loc[costs.units.str.contains('EUR')==True, 'units'] = costs.loc[costs.units.str.contains('EUR')==True, 'units'].str.replace('EUR', 'ZAR')

    costs.loc[costs.units.str.contains("tCO2")==True, costs_yr ] *= 1e3
    costs.loc[costs.units.str.contains("tCO2")==True, 'units'] = costs.loc[costs.units.str.contains('tCO2')==True, 'units'].str.replace('tCO2', 'kgCO2')

    # Convert fuel cost from R/GJ to R/MWh
    costs.loc[costs.units.str.contains("R/GJ")==True, costs_yr ] *= 3.6 
    costs.loc[costs.units.str.contains("R/GJ")==True, 'units'] = 'R/MWht' 
    return costs

def map_component_parameters(tech, first_year, last_year, tech_flag):

    rename_dict = dict(
        fom = "fixed_om_cost (R/kW/yr)",
        p_nom = 'capacity (MW)',
        name ='station_name',
        carrier = 'carrier',
        build_year = 'commissioning_date',
        decom_date = 'decommissioning_date',
        x = 'gps_lon',
        y = 'gps_lat',
        status = 'status',
        heat_rate = 'marginal_heat_rate (GJ/MWh)',
        no_load_heat_rate = 'no_load_heat_rate (GJ/h)',
        fuel_price = 'fuel_price (R/GJ)',
        vom = 'variable_om_cost (R/MWh)',
        max_ramp_up = 'max_ramp_up (%/h)',
        max_ramp_down = 'max_ramp_down (%/h)',
        max_ramp_start_up = 'max_ramp_start_up (%/h)',
        max_ramp_shut_down = 'max_ramp_shut_down (%/h)',
        start_up_cost = 'start_up_cost (R)',
        shut_down_cost = 'shut_down_cost (R)',
        min_stable_level = 'min_stable_level (%)',
        min_up_time = 'min_up_time (h)',
        min_down_time = 'min_down_time (h)',
        unit_size = 'unit_size (MW)',
        units = 'number_units',
        st_efficiency="round_trip_efficiency (%)",
        max_hours="storage_hours",
        CSP_max_hours='csp_storage_hours',
        committable = "dispatch_committable",
        #emissions = "co2_emissions_output (kgCO2/MWh)",
        input_emissions = "co2_emissions_input (kgCO2/GJ)",
    )

    if tech_flag == 'Generator':
        tech['efficiency'] = (3.6/tech.pop(rename_dict['heat_rate'])).fillna(1) # convert GJ/MWh to %
        tech["stand_by_energy"] = tech.pop(rename_dict['no_load_heat_rate']).fillna(0) # GJ/h
        tech['ramp_limit_up'] = tech.pop(rename_dict['max_ramp_up'])
        tech['ramp_limit_down'] = tech.pop(rename_dict['max_ramp_down'])     
        tech['p_min_pu'] = tech.pop(rename_dict['min_stable_level']).fillna(0)
        tech['ramp_limit_start_up'] = tech.pop(rename_dict['max_ramp_start_up'])
        tech['ramp_limit_shut_down'] = tech.pop(rename_dict['max_ramp_shut_down'])    
        tech['start_up_cost'] = tech.pop(rename_dict['start_up_cost']).fillna(0)
        tech['shut_down_cost'] = tech.pop(rename_dict['shut_down_cost']).fillna(0)
        tech['min_up_time'] = tech.pop(rename_dict['min_up_time']).fillna(0)
        tech['min_down_time'] = tech.pop(rename_dict['min_down_time']).fillna(0)
        tech["fuel_price"] = tech.pop(rename_dict['fuel_price']).fillna(0)
        tech["vom"] = tech.pop(rename_dict['vom']).fillna(0)

        #tech['marginal_cost'] = (3.6*tech.pop(rename_dict['fuel_price'])/tech['efficiency']).fillna(0) + tech.pop(rename_dict['vom'])
        tech["committable"] = tech.pop(rename_dict["committable"]).fillna(0)
        tech["input_emissions"] = tech.pop(rename_dict["input_emissions"]).fillna(0)
        #tech["output_emissions"] = tech.pop(rename_dict["emissions"]).fillna(0) #moved from Excel input to calculated here.
            
        # Calculate marginal cost if static fuel costs are specified
        static_fuel = find_non_string_entries(tech["fuel_price"]) # fuel prices can vary over time as well, so we only consider static input prices here
        tech[["marginal_cost","stand_by_cost","stand_by_emissions"]] = 0
        tech.loc[static_fuel, 'marginal_cost'] = 3.6 * (tech.loc[static_fuel, 'fuel_price'] / tech.loc[static_fuel, 'efficiency']).fillna(0) + tech.loc[static_fuel, 'vom'] # Fuel cost R/GJ conversion to R/MWh hence *3.6 factor
        tech.loc[static_fuel, 'stand_by_cost'] = tech.loc[static_fuel, 'fuel_price'] * tech.loc[static_fuel, 'stand_by_energy'].fillna(0) #R/GJ * GJ/h -> R/h 
        
        # Calculate emissions based on input emissions        
        static_input_emissions = find_non_string_entries(tech["input_emissions"]) # fuel emissions can vary over time as well, so we only consider static input emissions here
        tech.loc[static_input_emissions, "output_emissions"] = 3.6 / tech.loc[static_input_emissions, "efficiency"] * tech.loc[static_input_emissions, "input_emissions"].astype(float) #GJ/MWh * kgCO2/GJ -> kgCO2/MWh
        tech.loc[static_input_emissions, "stand_by_emissions"] = tech.loc[static_input_emissions, "stand_by_energy"] * tech.loc[static_input_emissions, "input_emissions"].astype(float) # GJ/h * kgCO2/GJ -> kgCO2/h

    else:
        tech["efficiency"] = tech.pop(rename_dict["st_efficiency"])
        tech["max_hours"] = tech.pop(rename_dict["max_hours"])
        tech['marginal_cost'] = tech.pop(rename_dict['vom'])

    tech['capital_cost'] = 1e3*tech.pop(rename_dict['fom'])
    tech = tech.rename(
        columns={rename_dict[f]: f for f in {'p_nom', 'name', 'carrier', 'x', 'y','build_year','decom_date'}}
    )

    tech['build_year'] = tech['build_year'].replace({'pre': first_year-1}).values
    tech['build_year'] = tech['build_year'].fillna(first_year-1).values
    tech['decom_date'] = tech['decom_date'].replace({'post': last_year+1}).values
    tech['lifetime'] = tech['decom_date'] - tech['build_year']

    return tech

def remove_leap_day(df):
    return df[~((df.index.month == 2) & (df.index.day == 29))]
    
def save_to_geojson(df, fn):
    if os.path.exists(fn):
        os.unlink(fn)  # remove file if it exists
    if not isinstance(df, gpd.GeoDataFrame):
        df = gpd.GeoDataFrame(dict(geometry=df))

    # save file if the GeoDataFrame is non-empty
    if df.shape[0] > 0:
        df = df.reset_index()
        #schema = {**gpd.io.file.infer_schema(df), "geometry": "Unknown"}
        df.to_file(fn, driver="GeoJSON")#, schema=schema)
    else:
        # create empty file to avoid issues with snakemake
        with open(fn, "w") as fp:
            pass

def drop_non_pypsa_attrs(n, c, df):
    df = df.loc[:, df.columns.isin(n.components[c]["attrs"].index)]
    return df

def normalize_and_rename_df(df, snapshots, fillna, suffix=None):
    df = df.loc[snapshots]
    df = (df / df.max()).fillna(fillna)
    if suffix:
        df.columns += f'_{suffix}'
    return df, df.max()

def find_string_entries(series):
    return series[series.apply(lambda x: isinstance(x, str))]#.index

def find_non_string_entries(series):
    return series[~series.apply(lambda x: isinstance(x, str))].index

def assign_segmented_df_to_network(df, search_str, replace_str, target):
    cols = df.columns[df.columns.str.contains(search_str)]
    segmented = df[cols]
    segmented.columns = segmented.columns.str.replace(search_str, replace_str)
    target = segmented


def get_start_year(sns, multi_invest):
    return sns.get_level_values(0)[0] if multi_invest else sns[0].year

def get_end_year(sns, multi_invest):
    return sns.get_level_values(0)[-1] if multi_invest else sns[0].year

def get_snapshots(sns, multi_invest):
    return sns.get_level_values(1) if multi_invest else sns

def get_investment_periods(sns, multi_invest):
    return sns.get_level_values(0).unique().to_list() if multi_invest else [sns[0].year]

def adjust_by_p_max_pu(n, config):
    for carrier in config.keys():
        com_i = n.generators.query("carrier == @carrier & committable").index
        non_com_i = n.generators.query("carrier == @carrier & committable == False").index
        for p in config[carrier]:
            n.generators.loc[com_i, p] = (
                n.generators.loc[com_i, p] * get_as_dense(n, "Generator", "p_max_pu")[com_i].mean()
            )

            n.generators_t[p][non_com_i] = (
                get_as_dense(n, "Generator", p)[non_com_i] * get_as_dense(n, "Generator", "p_max_pu")[non_com_i]
            )
     

def initial_ramp_rate_fix(n):
    """
    Under certain conditions the ramp rates of the generators between periods can lead to infeasibilities. 
    This function sets the ramp rates of the first snapshot in each period to 1 to avoid this scenario.
    """
    ramp_up_dense = get_as_dense(n, "Generator", "ramp_limit_up")
    ramp_down_dense = get_as_dense(n, "Generator", "ramp_limit_down")
    
    for y in n.investment_periods:
        first_sns = (y, f"{y}-01-01 00:00:00")
        ramp_up_dense.loc[first_sns] = 1
        ramp_down_dense.loc[first_sns] = 1
    
    n.generators_t.ramp_limit_up = ramp_up_dense
    n.generators_t.ramp_limit_down = ramp_down_dense

    #p_min_pu_dense = get_as_dense(n, "Generator", "p_min_pu")

    # limit_up = ~ramp_up_dense.isnull().all()
    # limit_down = ~ramp_down_dense.isnull().all()
    
    # for y, y_prev in zip(n.investment_periods[1:], n.investment_periods[:-1]):
    #     first_sns = (y, f"{y}-01-01 00:00:00")
    #     gen_list = np.unique(list(n.generators.query("build_year <= @y & build_year > @y_prev").index) + list(n.generators.query("carrier == 'coal'").index))

    #     gens_up = gen_list[limit_up[gen_list]]

    #     n.generators_t.ramp_limit_up.loc[y, gens_up] = ramp_up_dense.loc[y, gens_up]
    #     n.generators_t.ramp_limit_up.loc[(y,first_sns), gens_up] = np.maximum(p_min_pu_dense.loc[first_sns, gens_up], ramp_up_dense.loc[first_sns, gens_up])
        
    #     gens_down = gen_list[limit_down[gen_list]]
    #     n.generators_t.ramp_limit_down.loc[y,gens_down] = ramp_down_dense.loc[y, gens_down]
    #     n.generators_t.ramp_limit_down.loc[(y, first_sns), gens_down] = np.maximum(p_min_pu_dense.loc[first_sns, gens_up], ramp_up_dense.loc[first_sns, gens_up])


def apply_default_attr(df, attrs, snakemake):
    params = [
        "bus", 
        "carrier", 
        "lifetime", 
        "p_nom", 
        "efficiency", 
        "ramp_limit_up", 
        "ramp_limit_down", 
        "marginal_cost", 
        "capital_cost"
    ]
    uc_params = [
        "ramp_limit_start_up",
        "ramp_limit_shut_down", 
        "start_up_cost", 
        "shut_down_cost", 
        "min_up_time", 
        "min_down_time",
        "p_min_pu",
        "committable",
    ]

    params += uc_params
    default_attrs = attrs[["default","type"]]
    default_list = default_attrs.loc[default_attrs.index.isin(params), "default"].dropna().index

    default_list = default_list[default_list.isin(df.columns)]

    conv_type = {'int': int, 'float': float, "static or series": float, "series": float, 'boolean': bool, 'string': str}
    for attr in default_list:
        default = default_attrs.loc[attr, "default"]
        df[attr] = df[attr].fillna(conv_type[default_attrs.loc[attr, "type"]](default))
    
    return df

def add_noise(df, std_dev, steps):
    noise = pd.Series(index=df.index, dtype=float)
    idxs = noise.iloc[::steps].index
    noise.loc[idxs] = df.loc[idxs] + np.random.normal(loc=0, scale=std_dev, size=len(idxs))
    return noise.interpolate()

def get_carriers_from_model_file(scenario_setup):

    carriers = {
        "fixed":{
            "conventional":[],
            "renewables":[],
            "storage":[],
            },
        "extendable":{
            "conventional":[],
            "renewables":[],
            "storage":[],
            }
    }
    
    for tech in ["conventional", "renewables", "storage"]:
        carriers["fixed"][tech] = list(pd.read_excel(
            os.path.join(scenario_setup["sub_path"],"fixed_technologies.xlsx"),
            sheet_name=f"{tech}",
            na_values=["-"],
            index_col=[0,1]
        ).loc[scenario_setup[f"fixed_{tech}"]]["carrier"].unique())

    ext_carriers = (
        pd.read_excel(
            os.path.join(scenario_setup["sub_path"],"extendable_technologies.xlsx"), 
            sheet_name='active',
            index_col=[0,1,2],
    ))[scenario_setup["extendable_active"]]

    ext_carriers = ext_carriers[ext_carriers==True].reset_index()
    ext_carriers= ext_carriers.drop_duplicates(subset='carrier', keep='first').reset_index()[["carrier", "category"]]


    ext_carriers.set_index("category", inplace=True)
    for tech in ["conventional", "renewables", "storage"]:
        carriers["extendable"][tech] = list(ext_carriers.loc[tech].carrier.unique()) if len(ext_carriers.loc[tech])>1 else ext_carriers.loc[tech].carrier

    return carriers


def check_folder(path):
    # Check if the folder exists, if not create the folder
    if not os.path.exists(path):
        os.makedirs(path)
        print(f"Folder '{path}' created.")


def load_scenario_setup(scenarios_file, scenario, include_run_only=True):
    scenario_setup = (
        pd.read_excel(
            scenarios_file, 
            sheet_name="scenario_definition",
            index_col=[1])
    )

    if include_run_only:
        return (scenario_setup[scenario_setup.run_scenario.astype(bool)]).loc[scenario]
    else:
        return scenario_setup.loc[scenario]

# Temp PyPSA fix ahead of master
def single_year_network_copy(
    n,
    snapshots=None,
    investment_periods=None,
    ignore_standard_types=False,
):
    """
    Returns a deep copy of the Network object with all components and time-
    dependent data.

    Returns
    --------
    network : pypsa.Network

    Parameters
    ----------
    with_time : boolean, default True
        Copy snapshots and time-varying network.component_names_t data too.
    snapshots : list or index slice
        A list of snapshots to copy, must be a subset of
        network.snapshots, defaults to network.snapshots
    ignore_standard_types : boolean, default False
        Ignore the PyPSA standard types.

    Examples
    --------
    >>> network_copy = network.copy()
    """
    (
        override_components,
        override_component_attrs,
    ) = n._retrieve_overridden_components()

    network = n.__class__(
        ignore_standard_types=ignore_standard_types,
        override_components=override_components,
        override_component_attrs=override_component_attrs,
    )

    other_comps = sorted(n.all_components - {"Bus", "Carrier"})
    for component in n.iterate_components(["Bus", "Carrier"] + other_comps):
        df = component.df
        # drop the standard types to avoid them being read in twice
        if (
            not ignore_standard_types
            and component.name in n.standard_type_components
        ):
            df = component.df.drop(
                network.components[component.name]["standard_types"].index
            )
        if investment_periods is not None:
            df = df.loc[n.get_active_assets(component.name, investment_periods)]
        import_components_from_dataframe(network, df, component.name)

    if snapshots is None:
        snapshots = n.snapshots
    if investment_periods is None:
        investment_periods = n.investment_period_weightings.index
    network.set_snapshots(snapshots)
    if not investment_periods.empty:
        network.set_investment_periods(investment_periods)
    for component in n.iterate_components():
        pnl = getattr(network, component.list_name + "_t")
        for k in component.pnl.keys():
            if component.name in ["Generator", "Link", "StorageUnit", "Store"]:
                active = n.df(component.name)[n.get_active_assets(component.name, investment_periods)].index
                active = component.pnl[k].columns.intersection(active)
                pnl[k] = component.pnl[k].loc[snapshots,active].copy()
            else:
                pnl[k] = component.pnl[k].loc[snapshots].copy()
    network.snapshot_weightings = n.snapshot_weightings.loc[snapshots].copy()
    network.investment_period_weightings = (
        n.investment_period_weightings.loc[investment_periods].copy()
    )

    # catch all remaining attributes of network
    for attr in ["name", "srid"]:
        setattr(network, attr, getattr(n, attr))

    return network



# Not needed once PyPSA main is updated
def single_year_network_copy(
    n,
    snapshots=None,
    investment_periods=None,
    ignore_standard_types=False,
):
    """
    Returns a deep copy of the Network object with all components and time-
    dependent data.

    Returns
    --------
    network : pypsa.Network

    Parameters
    ----------
    with_time : boolean, default True
        Copy snapshots and time-varying network.component_names_t data too.
    snapshots : list or index slice
        A list of snapshots to copy, must be a subset of
        network.snapshots, defaults to network.snapshots
    ignore_standard_types : boolean, default False
        Ignore the PyPSA standard types.

    Examples
    --------
    >>> network_copy = network.copy()
    """
    (
        override_components,
        override_component_attrs,
    ) = n._retrieve_overridden_components()

    network = n.__class__(
        ignore_standard_types=ignore_standard_types,
        override_components=override_components,
        override_component_attrs=override_component_attrs,
    )

    other_comps = sorted(n.all_components - {"Bus", "Carrier"})
    for component in n.iterate_components(["Bus", "Carrier"] + other_comps):
        df = component.df
        # drop the standard types to avoid them being read in twice
        if (
            not ignore_standard_types
            and component.name in n.standard_type_components
        ):
            df = component.df.drop(
                network.components[component.name]["standard_types"].index
            )
        if investment_periods is not None:
            df = df.loc[n.get_active_assets(component.name, investment_periods)]
        import_components_from_dataframe(network, df, component.name)

    if snapshots is None:
        snapshots = n.snapshots
    if investment_periods is None:
        investment_periods = n.investment_period_weightings.index
    network.set_snapshots(snapshots)
    if not investment_periods.empty:
        network.set_investment_periods(investment_periods)
    for component in n.iterate_components():
        pnl = getattr(network, component.list_name + "_t")
        for k in component.pnl.keys():
            if component.name in ["Generator", "Link", "StorageUnit", "Store"]:
                active = n.df(component.name)[n.get_active_assets(component.name, investment_periods)].index
                active = component.pnl[k].columns.intersection(active)
                pnl[k] = component.pnl[k].loc[snapshots,active].copy()
            else:
                pnl[k] = component.pnl[k].loc[snapshots].copy()
    network.snapshot_weightings = n.snapshot_weightings.loc[snapshots].copy()
    network.investment_period_weightings = (
        n.investment_period_weightings.loc[investment_periods].copy()
    )

    # catch all remaining attributes of network
    for attr in ["name", "srid"]:
        setattr(network, attr, getattr(n, attr))

    return network


import numpy as np

def get_first_last_day_positions():
    """
    Identify the positions of the first and last 24 hours of each month in a year
    for an array of 8760 hours.

    Returns:
        dict: A dictionary with months (1-12) as keys and tuples (first_24h, last_24h)
              as values. Each tuple contains the positions of the first and last 24 hours
              for the corresponding month.
    """
    # Days in each month for a non-leap year
    days_in_month = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    hours_in_month = np.array(days_in_month) * 24

    # Initialize positions
    positions = {}
    start = 0

    for month, hours in enumerate(hours_in_month, start=1):
        # Calculate positions for the first and last 24 hours
        first_24h = list(range(start, start + 24))
        last_24h = list(range(start + hours - 24, start + hours))
        positions[month] = (first_24h, last_24h)
        start += hours

    return positions

