# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: : 2017-2020 The PyPSA-Eur Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later
import os
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from pypsa.descriptors import (Dict,get_active_assets)
from pypsa.descriptors import get_switchable_as_dense as get_as_dense

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

def pdbcast(v, h):
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

    try:
        capacity.loc['OCGT',:]+=capacity.loc['gas',:]+capacity.loc['diesel',:]
    except:
        capacity.loc['OCGT',:]+=capacity.loc['gas',:]
        
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
    # if len(df) > 0:
    if not df.empty:
        return df.to_csv(path, **kwargs, na_rep=NA_VALUES[0])
    else:
        with open(path, "w") as fp:
            pass


def save_to_geojson(df, fn):
    if os.path.exists(fn):
        os.unlink(fn)  # remove file if it exists

    # save file if the (Geo)DataFrame is non-empty
    if df.empty:
        # create empty file to avoid issues with snakemake
        with open(fn, "w") as fp:
            pass
    else:
        # save file
        df.to_file(fn, driver="GeoJSON")


def read_geojson(fn):
    # if the file is non-zero, read the geodataframe and return it
    if os.path.getsize(fn) > 0:
        return gpd.read_file(fn)
    else:
        # else return an empty GeoDataFrame
        return gpd.GeoDataFrame(geometry=[])

def pdbcast(v, h):
    return pd.DataFrame(v.values.reshape((-1, 1)) * h.values,
                        index=v.index, columns=h.index)

def map_generator_parameters(gens,first_year):
    ps_f = dict(
        PHS_efficiency="Pump Efficiency (%)",
        PHS_units="Pump Units",
        PHS_load="Pump Load per unit (MW)",
        PHS_max_hours="Pumped Storage - Max Storage (GWh)"
    )
    csp_f = dict(CSP_max_hours='CSP Storage (hours)')
    g_f = dict(
        fom="Fixed O&M Cost (R/kW/yr)",
        p_nom='Capacity (MW)',
        name='Power Station Name',
        carrier='Carrier',
        build_year='Future Commissioning Date',
        decom_date='Decommissioning Date',
        x='GPS Longitude',
        y='GPS Latitude',
        status='Status',
        heat_rate='Heat Rate (GJ/MWh)',
        fuel_price='Fuel Price (R/GJ)',
        vom='Variable O&M Cost (R/MWh)',
        max_ramp_up='Max Ramp Up (MW/min)',
        max_ramp_down='Max Ramp Down (MW/min)',
        min_stable='Min Stable Level (%)',
        unit_size='Unit size (MW)',
        units='Number units',
        maint_rate='Typical annual maintenance rate (%)',
        out_rate='Typical annual forced outage rate (%)',
    )

    # Calculate fields where pypsa uses different conventions
    gens['efficiency'] = (3.6/gens.pop(g_f['heat_rate'])).fillna(1)
    gens['marginal_cost'] = (3.6*gens.pop(g_f['fuel_price'])/gens['efficiency']).fillna(0) + gens.pop(g_f['vom'])
    gens['capital_cost'] = 1e3*gens.pop(g_f['fom'])
    gens['ramp_limit_up'] = 60*gens.pop(g_f['max_ramp_up'])/gens[g_f['p_nom']]
    gens['ramp_limit_down'] = 60*gens.pop(g_f['max_ramp_down'])/gens[g_f['p_nom']]    

    gens = gens.rename(
        columns={g_f[f]: f for f in {'p_nom', 'name', 'carrier', 'x', 'y','build_year','decom_date','min_stable'}})
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