# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: : 2017-2023 The PyPSA-Eur Authors
#
# SPDX-License-Identifier: MIT
"""
Solves linear optimal dispatch in hourly resolution using the capacities of
previous capacity expansion in rule :mod:`solve_network`.
"""


import logging
import pandas as pd
import numpy as np
import pypsa
from xarray import DataArray
#from _helpers import configure_logging, update_config_with_sector_opts
#from solve_network import prepare_network, solve_network
from pypsa.descriptors import get_switchable_as_dense as get_as_dense, get_activity_mask

logger = logging.getLogger(__name__)

from _helpers import (
    _add_missing_carriers_from_costs,
    convert_cost_units,
    load_disaggregate, 
    map_component_parameters, 
    read_and_filter_generators,
    remove_leap_day,
    drop_non_pypsa_attrs,
    normed,
    get_start_year,
    get_snapshots,
    get_investment_periods,
    adjust_by_p_max_pu,
    apply_default_attr
)

from prepare_and_solve_network import (
    set_operational_limits,
    ccgt_steam_constraints,
    rmippp_constraints,
)

def get_min_stable_level(n, model_file, model_setup, existing_carriers, extended_carriers):
    
    existing_param = pd.read_excel(
        model_file, 
        sheet_name="fixed_conventional",
        na_values=["-"],
        index_col=[0,1]
    ).loc[model_setup["fixed_conventional"]]
    
    existing_gens = n.generators.query("carrier in @existing_carriers & p_nom_extendable == False").index
    existing_msl= existing_param.loc[existing_gens, "Min Stable Level (%)"].rename("p_min_pu")
    
    extended_param = pd.read_excel(
        model_file, 
        sheet_name = "extendable_parameters",
        index_col = [0,2,1],
    ).sort_index().loc[model_setup["extendable_parameters"]]

    extended_gens = n.generators.query("carrier in @extended_carriers & p_nom_extendable").index
    extended_msl = pd.Series(index=extended_gens, name = "p_min_pu")
    for g in extended_gens:
        carrier = g.split("-")[1]
        y = int(g.split("-")[2])
        if y in extended_param.columns:
            extended_msl[g] = extended_param.loc[("min_stable_level", carrier), y].astype(float)
        else:
            interp_data = extended_param.loc[("min_stable_level", carrier), :].drop(["unit", "source"]).astype(float)
            interp_data = interp_data.append(pd.Series(index=[y], data=[np.nan])).interpolate()
            extended_msl[g] = interp_data.loc[y]

    return existing_msl, extended_msl


def set_max_status(n, sns, p_max_pu):

    # init period = 100h to let model stabilise status
    if sns[0] == n.snapshots[0]:
        init_periods=100
        n.generators_t.p_max_pu.loc[
            sns[:init_periods], p_max_pu.columns
        ] = p_max_pu.loc[sns[:init_periods], :].values
        
        n.generators_t.p_min_pu.loc[:,p_max_pu.columns] = get_as_dense(n, "Generator", "p_min_pu").loc[:,p_max_pu.columns]
        n.generators_t.p_min_pu.loc[
            sns[:init_periods], p_max_pu.columns
        ] = 0
        sns = sns[init_periods:]

    active = get_activity_mask(n, "Generator", sns, p_max_pu.columns)
    active.rename_axis("Generator-com", axis = 1, inplace = True)
    p_max_pu = p_max_pu.loc[sns, active.any(axis=0)]
    p_max_pu = p_max_pu.loc[sns, (p_max_pu != 1).any(axis=0)]

    status = n.model.variables["Generator-status"].sel({"Generator-com":p_max_pu.columns})
    lhs = status.sel(snapshot=sns)
    if p_max_pu.columns.name != "Generator-com":
        p_max_pu.columns.name = "Generator-com"
    rhs = DataArray(p_max_pu)
    
    n.model.add_constraints(lhs, "<=", rhs, name="max_status")

def set_upper_combined_status_bus(n, sns, p_max_pu):

    active = get_activity_mask(n, "Generator", sns, p_max_pu.columns)
    active.rename_axis("Generator-com", axis = 1, inplace = True)
    p_max_pu = p_max_pu.loc[sns, active.any(axis=0)]
    p_max_pu = p_max_pu.loc[:, (p_max_pu != 1).any(axis=0)]

    for bus_i in n.buses.index:
        bus_gens = n.generators.query("bus == @bus_i").index.intersection(p_max_pu.columns)
        if len(bus_gens) >= 0: 
            p_nom = n.generators.loc[bus_gens, "p_nom"]
            p_nom.name = "Generator-com"
            status = n.model.variables["Generator-status"].sel({"snapshot":sns, "Generator-com":bus_gens})

            p_nom_df = pd.DataFrame(index = sns, columns = p_nom.index)        
            p_nom_df.loc[:] = p_nom.values
            p_nom_df.rename_axis("Generator-com", axis = 1, inplace = True)

            active.columns.name = "Generator-com"
            lhs = (DataArray(p_nom_df) * status).sum("Generator-com")
            rhs = (p_nom * p_max_pu[bus_gens]).sum(axis=1)
            
            n.model.add_constraints(lhs, "<=", rhs, name=f"{bus_i}-max_status")


def set_upper_avg_status_over_sns(n, sns, p_max_pu):
    
    active = get_activity_mask(n, "Generator", sns, p_max_pu.columns)
    active.rename_axis("Generator-com", axis = 1, inplace = True)
    p_max_pu = p_max_pu.loc[sns, active.any(axis=0)]
    p_max_pu = p_max_pu.loc[:, (p_max_pu != 1).any(axis=0)]

    weightings = pd.DataFrame(index = sns, columns = p_max_pu.columns)
    weight_values = n.snapshot_weightings.generators.loc[sns].values.reshape(-1, 1)
    weightings.loc[:] = weight_values
    weightings.rename_axis("Generator-com", axis = 1, inplace = True)

    status = n.model.variables["Generator-status"].sel({"Generator-com":p_max_pu.columns, "snapshot":sns})
    lhs = (status * weightings).sum("snapshot")
    if p_max_pu.columns.name != "Generator-com":
        p_max_pu.columns.name = "Generator-com"
    rhs = (weightings * p_max_pu).sum()

    n.model.add_constraints(lhs, "<=", rhs, name="upper_avg_status_sns")

def set_max_status4(n, sns, p_max_pu):
    
    # init period = 100h to let model stabilise status
    # if sns[0] == n.snapshots[0]:
    #     init_periods=100
    #     n.generators_t.p_max_pu.loc[
    #         sns[:init_periods], p_max_pu.columns
    #     ] = p_max_pu.loc[sns[:init_periods], :].values
        
    #     n.generators_t.p_min_pu.loc[:,p_max_pu.columns] = get_as_dense(n, "Generator", "p_min_pu").loc[:,p_max_pu.columns]
    #     n.generators_t.p_min_pu.loc[
    #         sns[:init_periods], p_max_pu.columns
    #     ] = 0
    #     sns = sns[init_periods:]

    active = get_activity_mask(n, "Generator", sns, p_max_pu.columns)
    p_max_pu = p_max_pu.loc[sns, active.any(axis=0)]

    active.columns.name = "Generator-com"
    status = n.model.variables["Generator-status"].sel({"Generator-com":p_max_pu.columns})
    lhs = status.sel(snapshot=sns).groupby("snapshot.week").sum()
    if p_max_pu.columns.name != "Generator-com":
        p_max_pu.columns.name = "Generator-com"
    rhs = p_max_pu.groupby(p_max_pu.index.isocalendar().week).sum()
    
    n.model.add_constraints(lhs, "<=", rhs, name="max_status")

def set_existing_committable(n, sns, model_file, model_setup, config):

    existing_carriers = config['existing']
    existing_gen = n.generators.query("carrier in @existing_carriers & p_nom_extendable == False").index.to_list()

    extended_carriers = config['extended']
    extended_gen = n.generators.query("carrier in @extended_carriers & p_nom_extendable").index.to_list()
    
    n.generators.loc[existing_gen + extended_gen, "committable"] = True

    p_max_pu = get_as_dense(n, "Generator", "p_max_pu", sns)[existing_gen + extended_gen].copy()
    n.generators_t.p_max_pu.loc[:, existing_gen + extended_gen] = 1
    n.generators.loc[existing_gen + extended_gen, "p_max_pu"] = 1

    existing_msl, extended_msl = get_min_stable_level(n, model_file, model_setup, existing_carriers, extended_carriers)

    n.generators.loc[existing_gen, "p_min_pu"] = existing_msl
    n.generators.loc[extended_gen, "p_min_pu"] = extended_msl

    return p_max_pu

if __name__ == "__main__":
    if 'snakemake' not in globals():
        from _helpers import mock_snakemake
        snakemake = mock_snakemake(
            'solve_network_dispatch', 
            **{
                'model_file':'TEST',
                'regions':'1',
                'resarea':'redz',
                'll':'highs',
                'opts':'LC',
                'years':'2024',
            }
        )
    n = pypsa.Network(snakemake.input.network)

    model_file = pd.ExcelFile(snakemake.input.model_file)
    model_setup = (
        pd.read_excel(
            model_file, 
            sheet_name="model_setup",
            index_col=[0])
            .loc[snakemake.wildcards.model_file]
    )

    config = snakemake.config["electricity"]["dispatch_committable_carriers"]
    p_max_pu = set_existing_committable(n, model_file, model_setup, config)
    n.optimize.fix_optimal_capacities()

    n.optimize.create_model(snapshots = n.snapshots[:2000], linearized_unit_commitment=True, multi_investment_periods=True)
    set_max_status(n, n.snapshots[:2000], p_max_pu)

    solver_name = snakemake.config["solving"]["solver"].pop("name")
    solver_options = snakemake.config["solving"]["solver"].copy()
    n.optimize.solve_model(solver_name=solver_name, solver_options=solver_options)

    n.export_to_netcdf(snakemake.output[0])