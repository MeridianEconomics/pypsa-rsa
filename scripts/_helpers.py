# SPDX-FileCopyrightText:  PyPSA-ZA2, PyPSA-ZA, PyPSA-Earth and PyPSA-Eur Authors
# # SPDX-License-Identifier: MIT
# -*- coding: utf-8 -*-

import os
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

from pypsa.descriptors import get_switchable_as_dense as get_as_dense
from pypsa.descriptors import get_activity_mask, get_active_assets

"""
List of general helper functions
- configure_logging ->
- normed ->
"""
def configure_logging(snakemake, skip_handlers=False):
    """
    Configure the basic behaviour for the logging module.

    Note: Must only be called once from the __main__ section of a script.

    The setup includes printing log messages to STDERR and to a log file defined
    by either (in priority order): snakemake.log.python, snakemake.log[0] or "logs/{rulename}.log".
    Additional keywords from logging.basicConfig are accepted via the snakemake configuration
    file under snakemake.config.logging.

    Parameters
    ----------
    snakemake : snakemake object
        Your snakemake object containing a snakemake.config and snakemake.log.
    skip_handlers : True | False (default)
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


def _add_missing_carriers_from_costs(n, costs, carriers):
    start_year = n.snapshots.get_level_values(0)[0] if n.multi_invest else n.snapshots[0].year
    missing_carriers = pd.Index(carriers).difference(n.carriers.index)
    if missing_carriers.empty: return

    emissions = costs.loc[("co2_emissions",missing_carriers),start_year]
    emissions.index = emissions.index.droplevel(0)
    n.madd("Carrier", missing_carriers, co2_emissions=emissions)

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
    return df[df["Carrier"].isin(filter_carriers)]


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

# def aggregate_costs(n, flatten=False, opts=None, existing_only=False):

#     components = dict(
#         Link=("p_nom", "p0"),
#         Generator=("p_nom", "p"),
#         StorageUnit=("p_nom", "p"),
#         Store=("e_nom", "p"),
#         Line=("s_nom", None),
#         Transformer=("s_nom", None),
#     )

#     costs = {}
#     for c, (p_nom, p_attr) in zip(
#         n.iterate_components(components.keys(), skip_empty=False), components.values()
#     ):
#         if c.df.empty:
#             continue
#         if not existing_only:
#             p_nom += "_opt"
#         costs[(c.list_name, "capital")] = (
#             (c.df[p_nom] * c.df.capital_cost).groupby(c.df.carrier).sum()
#         )
#         if p_attr is not None:
#             p = c.pnl[p_attr].sum()
#             if c.name == "StorageUnit":
#                 p = p.loc[p > 0]
#             costs[(c.list_name, "marginal")] = (
#                 (p * c.df.marginal_cost).groupby(c.df.carrier).sum()
#             )
#     costs = pd.concat(costs)

#     if flatten:
#         assert opts is not None
#         conv_techs = opts["conv_techs"]

#         costs = costs.reset_index(level=0, drop=True)
#         costs = costs["capital"].add(
#             costs["marginal"].rename({t: t + " marginal" for t in conv_techs}),
#             fill_value=0.0,
#         )

#     return costs


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
    from pypsa.descriptors import Dict
    from snakemake.script import Snakemake

    script_dir = Path(__file__).parent.resolve()
    assert (
        Path.cwd().resolve() == script_dir
    ), f"mock_snakemake has to be run from the repository scripts directory {script_dir}"
    os.chdir(script_dir.parent)
    for p in sm.SNAKEFILE_CHOICES:
        if os.path.exists(p):
            snakefile = p
            break
    workflow = sm.Workflow(snakefile, overwrite_configfiles=[], rerun_triggers=[])
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
    costs_yr = costs.columns.drop('unit')
    costs.loc[costs.unit.str.contains("/kW")==True, costs_yr ] *= 1e3
    costs.loc[costs.unit.str.contains("USD")==True, costs_yr ] *= USD_ZAR
    costs.loc[costs.unit.str.contains("EUR")==True, costs_yr ] *= EUR_ZAR

    costs.loc[costs.unit.str.contains('/kW')==True, 'unit'] = costs.loc[costs.unit.str.contains('/kW')==True, 'unit'].str.replace('/kW', '/MW')
    costs.loc[costs.unit.str.contains('USD')==True, 'unit'] = costs.loc[costs.unit.str.contains('USD')==True, 'unit'].str.replace('USD', 'ZAR')
    costs.loc[costs.unit.str.contains('EUR')==True, 'unit'] = costs.loc[costs.unit.str.contains('EUR')==True, 'unit'].str.replace('EUR', 'ZAR')

    # Convert fuel cost from R/GJ to R/MWh
    costs.loc[costs.unit.str.contains("R/GJ")==True, costs_yr ] *= 3.6 
    costs.loc[costs.unit.str.contains("R/GJ")==True, 'unit'] = 'R/MWhe' 
    return costs

def map_component_parameters(gens, first_year):
    ps_f = dict(
        PHS_efficiency="PHS Efficiency (%)",
        PHS_units="PHS Units",
        PHS_load="PHS Load per unit (MW)",
        PHS_max_hours="PHS - Max Storage (GWh)"
    )
    csp_f = dict(CSP_max_hours='CSP Storage (hours)')
    g_f = dict(
        fom = "Fixed O&M Cost (R/kW/yr)",
        p_nom = 'Capacity (MW)',
        name ='Power Station Name',
        carrier = 'Carrier',
        build_year = 'Commissioning Date',
        decom_date = 'Decommissioning Date',
        x = 'GPS Longitude',
        y = 'GPS Latitude',
        status = 'Status',
        heat_rate = 'Heat Rate (GJ/MWh)',
        fuel_price = 'Fuel Price (R/GJ)',
        vom = 'Variable O&M Cost (R/MWh)',
        max_ramp_up = 'Max Ramp Up (MW/min)',
        max_ramp_down = 'Max Ramp Down (MW/min)',
        max_ramp_start_up = 'Max Ramp Start Up (MW/min)',
        max_ramp_shut_down = 'Max Ramp Shut Down (MW/min)',
        start_up_cost = 'Start Up Cost (R)',
        shut_down_cost = 'Shut Down Cost (R)',
        p_min_pu = 'Min Stable Level (%)',
        min_up_time = 'Min Up Time (h)',
        min_down_time = 'Min Down Time (h)',
        unit_size = 'Unit size (MW)',
        units = 'Number units',
        maint_rate = 'Typical annual maintenance rate (%)',
        out_rate = 'Typical annual forced outage rate (%)',
    )

    # Calculate fields where pypsa uses different conventions
    gens['efficiency'] = (3.6/gens.pop(g_f['heat_rate'])).fillna(1)
    gens['marginal_cost'] = (3.6*gens.pop(g_f['fuel_price'])/gens['efficiency']).fillna(0) + gens.pop(g_f['vom'])
    gens['capital_cost'] = 1e3*gens.pop(g_f['fom'])
    gens['ramp_limit_up'] = 60*gens.pop(g_f['max_ramp_up'])/gens[g_f['unit_size']]
    gens['ramp_limit_down'] = 60*gens.pop(g_f['max_ramp_down'])/gens[g_f['unit_size']]    
    
    # unit commitment parameters
    gens['ramp_limit_start_up'] = 60*gens.pop(g_f['max_ramp_start_up'])/gens[g_f['unit_size']]
    gens['ramp_limit_shut_down'] = 60*gens.pop(g_f['max_ramp_shut_down'])/gens[g_f['unit_size']]    
    gens['start_up_cost'] = gens.pop(g_f['start_up_cost']).fillna(0)
    gens['shut_down_cost'] = gens.pop(g_f['shut_down_cost']).fillna(0)
    gens['min_up_time'] = gens.pop(g_f['min_up_time']).fillna(0)
    gens['min_down_time'] = gens.pop(g_f['min_down_time']).fillna(0)

    gens = gens.rename(
        columns={g_f[f]: f for f in {'p_nom', 'name', 'carrier', 'x', 'y','build_year','decom_date','p_min_pu'}})
    gens = gens.rename(columns={ps_f[f]: f for f in {'PHS_efficiency','PHS_max_hours'}})    
    gens = gens.rename(columns={csp_f[f]: f for f in {'CSP_max_hours'}})     

    gens['build_year'] = gens['build_year'].fillna(first_year).values
    gens['decom_date'] = gens['decom_date'].replace({'beyond 2050': 2051}).values
    gens['lifetime'] = gens['decom_date'] - gens['build_year']

    return gens

def remove_leap_day(df):
    return df[~((df.index.month == 2) & (df.index.day == 29))]
    
def clean_pu_profiles(n):
    pu_index = n.generators_t.p_max_pu.columns.intersection(n.generators_t.p_min_pu.columns)
    for carrier in n.generators_t.p_min_pu.columns:
        if carrier in pu_index:
            error_loc=n.generators_t.p_min_pu[carrier][n.generators_t.p_min_pu[carrier]>n.generators_t.p_max_pu[carrier]].index
            n.generators_t.p_min_pu.loc[error_loc,carrier]=n.generators_t.p_max_pu.loc[error_loc,carrier]
        else:
            error_loc=n.generators_t.p_min_pu[carrier][n.generators_t.p_min_pu[carrier]>n.generators.p_max_pu[carrier]].index
            n.generators_t.p_min_pu.loc[error_loc,carrier]=n.generators.p_max_pu.loc[carrier]

def save_to_geojson(df, fn):
    if os.path.exists(fn):
        os.unlink(fn)  # remove file if it exists
    if not isinstance(df, gpd.GeoDataFrame):
        df = gpd.GeoDataFrame(dict(geometry=df))

    # save file if the GeoDataFrame is non-empty
    if df.shape[0] > 0:
        df = df.reset_index()
        schema = {**gpd.io.file.infer_schema(df), "geometry": "Unknown"}
        df.to_file(fn, driver="GeoJSON", schema=schema)
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

def normalize_and_rename_df(df, snapshots, fillna, suffix=None):
    df = df.loc[snapshots]
    df = (df / df.max()).fillna(fillna)
    if suffix:
        df.columns += f'_{suffix}'
    return df, df.max()

def assign_segmented_df_to_network(df, search_str, replace_str, target):
    cols = df.columns[df.columns.str.contains(search_str)]
    segmented = df[cols]
    segmented.columns = segmented.columns.str.replace(search_str, replace_str)
    target = segmented


def get_start_year(sns, multi_invest):
    return sns.get_level_values(0)[0] if multi_invest else sns[0].year

def get_snapshots(sns, multi_invest):
    return sns.get_level_values(1) if multi_invest else sns

def get_investment_periods(sns, multi_invest):
    return sns.get_level_values(0).unique().to_list() if multi_invest else [sns[0].year]

def adjust_by_p_max_pu(n, config):
    for carrier in config.keys():
        gen_list = n.generators[n.generators.carrier == carrier].index
        for p in config[carrier]:#["p_min_pu", "ramp_limit_up", "ramp_limit_down"]:
            n.generators_t[p][gen_list] = (
                get_as_dense(n, "Generator", p)[gen_list] * get_as_dense(n, "Generator", "p_max_pu")[gen_list]
            )

def initial_ramp_rate_fix(n):
    ramp_up_dense = get_as_dense(n, "Generator", "ramp_limit_up")
    ramp_down_dense = get_as_dense(n, "Generator", "ramp_limit_down")
    p_min_pu_dense = get_as_dense(n, "Generator", "p_min_pu")

    limit_up = ~ramp_up_dense.isnull().all()
    limit_down = ~ramp_down_dense.isnull().all()
    
    for y, y_prev in zip(n.investment_periods[1:], n.investment_periods[:-1]):
        first_sns = (y, f"{y}-01-01 00:00:00")
        new_build = n.generators.query("build_year <= @y & build_year > @y_prev").index

        gens_up = new_build[limit_up[new_build]]
        n.generators_t.ramp_limit_up[gens_up] = ramp_up_dense[gens_up]
        n.generators_t.ramp_limit_up.loc[first_sns, gens_up] = np.maximum(p_min_pu_dense.loc[first_sns, gens_up], ramp_up_dense.loc[first_sns, gens_up])
        
        gens_down = new_build[limit_down[new_build]]
        n.generators_t.ramp_limit_down[gens_down] = ramp_down_dense[gens_down]
        n.generators_t.ramp_limit_down.loc[first_sns, gens_down] = np.maximum(p_min_pu_dense.loc[first_sns, gens_up], ramp_up_dense.loc[first_sns, gens_up])


def apply_default_attr(df, attrs):
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
        #"p_min_pu",
    ]
    params += uc_params
    
    default_attrs = attrs[["default","type"]]
    default_list = default_attrs.loc[default_attrs.index.isin(params), "default"].dropna().index

    conv_type = {'int': int, 'float': float, "static or series": float, "series": float}
    for attr in default_list:
        default = default_attrs.loc[attr, "default"]
        df[attr] = df[attr].fillna(conv_type[default_attrs.loc[attr, "type"]](default))
    
    return df
