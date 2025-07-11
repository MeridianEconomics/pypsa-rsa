configfile: "config.yaml"

from os.path import normpath, exists, isdir

wildcard_constraints:
    model_type="[a-zA-Z0-9_]+",
    scenarios_to_run="[-a-zA-Z0-9_]+",
import pandas as pd
import os
scenarios = pd.read_excel(
    os.path.join("scenarios",config["scenarios"]["folder"],config["scenarios"]["setup"]),
    sheet_name="scenario_definition", 
    index_col=0
)
scenarios_to_run = scenarios[scenarios["run_scenario"] == 1]

rule all:
    input:
        "results/solve_all_scenarios_complete"

rule solve_all_scenarios:
    input:
        expand(
            "results/" + config["scenarios"]["folder"] + "/network/capacity-{scenario}.nc",
            scenario=scenarios_to_run.index,
        )
    output:
        touch("results/solve_all_scenarios_complete")

rule build_topology:
    input:
        supply_regions=config["gis"]["path"] + "/supply_regions/rsa_supply_regions.gpkg",
        existing_lines=config["gis"]["path"] + "/transmission_grid/eskom_gcca_2022/Existing_Lines.shp",
        planned_lines=config["gis"]["path"] + "/transmission_grid/tdp_digitised/TDP_2023_32.shp",
        gdp_pop_data=config["gis"]["path"] + "/CSIR/Mesozones/Mesozones.shp",        
    output:
        buses="resources/"+config["scenarios"]["folder"]+"/buses-{scenario}.geojson",
        lines="resources/"+config["scenarios"]["folder"]+"/lines-{scenario}.geojson",
    threads: 1
    script: "scripts/build_topology.py"

rule base_network:
    input:
        buses="resources/" + config["scenarios"]["folder"] + "/buses-{scenario}.geojson",
        lines="resources/" + config["scenarios"]["folder"] + "/lines-{scenario}.geojson",
    output: "networks/" + config["scenarios"]["folder"] + "/base/{model_type}-{scenario}.nc",
    threads: 1
    resources: mem_mb=1000
    script: "scripts/base_network.py"

rule add_electricity:
    input:
        base_network="networks/" + config["scenarios"]["folder"] + "/base/{model_type}-{scenario}.nc",
        supply_regions="resources/" + config["scenarios"]["folder"] + "/buses-{scenario}.geojson",
        load="data/bundle/SystemEnergy2009_22.csv",
        eskom_profiles="data/eskom_pu_profiles.csv",
        renewable_profiles="data/bundle/renewable_profiles_updated.nc",
    output: "networks/"+ config["scenarios"]["folder"] + "/elec/{model_type}-{scenario}.nc",
    script: "scripts/add_electricity.py"

rule prepare_and_solve_network:
    input:
        network="networks/"+ config["scenarios"]["folder"] + "/elec/capacity-{scenario}.nc",
    output: 
        network="results/" + config["scenarios"]["folder"] + "/network/capacity-{scenario}.nc",
        network_stats="results/" + config["scenarios"]["folder"] +"/network_stats/{scenario}.csv",
        emissions="results/" + config["scenarios"]["folder"] +"/emissions/{scenario}.csv",
    resources:
        solver_slots=1
    script:
        "scripts/prepare_and_solve_network.py"