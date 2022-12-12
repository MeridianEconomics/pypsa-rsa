configfile: "config.yaml"

from os.path import normpath, exists, isdir

localrules: base_network, add_electricity, plot_network # , extract_summaries, add_sectors

ATLITE_NPROCESSES = config["atlite"].get("nprocesses", 4)

wildcard_constraints:
    resarea="[a-zA-Z0-9]+",
    model_file="[-a-zA-Z0-9]+",
    regions="[-+a-zA-Z0-9]+",
    opts="[-+a-zA-Z0-9]+"

if config['enable']['build_land_use']: 
    rule build_landuse_remove_protected_and_conservation_areas:
        input:
            landuse = "data/bundle/SALandCover_OriginalUTM35North_2013_GTI_72Classes/sa_lcov_2013-14_gti_utm35n_vs22b.tif",
            protected_areas = "data/bundle/SAPAD_OR_2017_Q2",
            conservation_areas = "data/bundle/SACAD_OR_2017_Q2"
        output: "resources/landuse_without_protected_conservation.tiff"
        benchmark: "benchmarks/landuse_remove_protected_and_conservation_areas"
        threads: 1
        resources: mem_mb=20000
        script: "scripts/build_landuse_remove_protected_and_conservation_areas.py"
    rule build_landuse_map_to_tech_and_supply_region:
        input:
            landuse = "resources/landuse_without_protected_conservation.tiff",
            supply_regions = "data/supply_regions/supply_regions_{regions}.shp",
            resarea = lambda w: "data/bundle/" + config['data']['resarea'][w.resarea]
        output:
            raster = "resources/raster_{tech}_percent_{regions}_{resarea}.tiff",
            area = "resources/area_{tech}_{regions}_{resarea}.csv"
        benchmark: "benchmarks/build_landuse_map_to_tech_and_supply_region/{tech}_{regions}_{resarea}"
        threads: 1
        resources: mem_mb=10000
        script: "scripts/build_landuse_map_to_tech_and_supply_region.py"


if config["enable"]["build_natura_raster"]:
    rule build_natura_raster:
        input:
            protected_areas = "data/bundle/SAPAD_OR_2017_Q2",
            conservation_areas = "data/bundle/SACAD_OR_2017_Q2",
            cutouts=expand("cutouts/{cutouts}.nc", **config["atlite"]),
        output:
            "resources/natura.tiff",
        resources:
            mem_mb=5000,
        log:
            "logs/build_natura_raster.log",
        script:
            "scripts/build_natura_raster.py"

if config['enable']['build_population']: 
   rule build_population:
       input:
           supply_regions='data/supply_regions/supply_regions_{regions}.shp',
           population='data/bundle/South_Africa_100m_Population/ZAF15adjv4.tif'
       output: 'resources/population_{regions}.csv'
       threads: 1
       resources: mem_mb=1000
       script: "scripts/build_population.py"

if config['enable']['build_cutout']:
    rule build_cutout:
        input:
            regions_onshore='data/supply_regions/supply_regions_RSA.shp',
            gwa_map = 'data/bundle/ZAF_wind-speed_100m.tif',
        output:
            "cutouts/{cutout}.nc",
        log:
            "logs/build_cutout/{cutout}.log",
        benchmark:
            "benchmarks/build_cutout_{cutout}"
        threads: ATLITE_NPROCESSES
        resources:
            mem_mb=ATLITE_NPROCESSES * 1000,
        script:
            "scripts/build_cutout.py"

if not config['hydro_inflow']['disable']:
    rule build_inflow_per_country:
        input: EIA_hydro_gen="data/EIA_hydro_generation_2011_2014.csv"
        output: "resources/hydro_inflow.csv"
        benchmark: "benchmarks/inflow_per_country"
        threads: 1
        resources: mem_mb=1000
        script: "scripts/build_inflow_per_country.py"

if config['enable']['build_topology']: 
    rule build_topology:
        input:
            supply_regions='data/supply_regions/supply_regions_{regions}.shp',
            centroids='data/supply_regions/centroids_{regions}.shp',
            num_lines='data/num_lines.xlsx',
        output:
            buses='resources/buses_{regions}.csv',
            lines='resources/lines_{regions}.csv',
            regions = 'resources/onshore_shapes_{regions}.geojson'
        threads: 1
        script: "scripts/build_topology.py"

rule base_network:
    input:
        buses='resources/buses_{regions}.csv',
        lines='resources/lines_{regions}.csv',
        population='resources/population_{regions}.csv'
    output: "networks/base_{regions}.nc"
    benchmark: "benchmarks/base_network_{regions}"
    threads: 1
    resources: mem_mb=1000
    script: "scripts/base_network.py"

if config['enable']['build_renewable_profiles'] & ~config['enable']['use_eskom_wind_solar']: 
    rule build_renewable_profiles:
        input:
            base_network="networks/base_{regions}.nc",
            regions = 'resources/onshore_shapes_{regions}.geojson',
            resarea = lambda w: "data/bundle/" + config['data']['resarea'][w.resarea],
            natura=lambda w: (
                "resources/landuse_without_protected_conservation.tiff"
                if config["renewable"][w.technology]["natura"]
                else []
            ),
            cutout=lambda w: "cutouts/"+ config["renewable"][w.technology]["cutout"] + ".nc",
            gwa_map="data/bundle/ZAF_wind-speed_100m.tif",
            salandcover = 'data/bundle/SALandCover_OriginalUTM35North_2013_GTI_72Classes/sa_lcov_2013-14_gti_utm35n_vs22b.tif'
        output:
            profile="resources/profile_{technology}_{regions}_{resarea}.nc",
            
        log:
            "logs/build_renewable_profile_{technology}_{regions}_{resarea}.log",
        benchmark:
            "benchmarks/build_renewable_profiles_{technology}_{regions}_{resarea}"
        threads: ATLITE_NPROCESSES
        resources:
            mem_mb=ATLITE_NPROCESSES * 5000,
        wildcard_constraints:
            technology="(?!hydro).*",  # Any technology other than hydro
        script:
            "scripts/build_renewable_profiles.py"

if ~config['enable']['use_eskom_wind_solar']:
    renewable_carriers = config["renewable"] 
else:
    renewable_carriers=[]

rule add_electricity:
    input:
        **{
            f"profile_{tech}": f"resources/profile_{tech}_"+ "{regions}_{resarea}.nc"
            for tech in renewable_carriers
        },
        base_network='networks/base_{regions}.nc',
        supply_regions='data/supply_regions/supply_regions_{regions}.shp',
        load='data/bundle/SystemEnergy2009_13.csv',
        onwind_area='resources/area_wind_{regions}_{resarea}.csv',
        solar_area='resources/area_solar_{regions}_{resarea}.csv',
        eskom_profiles="data/eskom_pu_profiles.csv",
        model_file="data/model_file.xlsx",
        existing_generators_eaf="data/Eskom EAF data.xlsx",
    output: "networks/elec_{model_file}_{regions}_{resarea}.nc",
    benchmark: "benchmarks/add_electricity/elec_{model_file}_{regions}_{resarea}"
    threads: 1
    resources: mem_mb=1000
    script: "scripts/add_electricity.py"

rule prepare_network:
    input:
        network="networks/elec_{model_file}_{regions}_{resarea}.nc",
        model_file="data/model_file.xlsx",
        onwind_area='resources/area_wind_{regions}_{resarea}.csv',
        solar_area='resources/area_solar_{regions}_{resarea}.csv',
    output:"networks/pre_{model_file}_{regions}_{resarea}_l{ll}_{opts}.nc",
    log:"logs/prepare_network/pre_{model_file}_{regions}_{resarea}_l{ll}_{opts}.log",
    benchmark:"benchmarks/prepare_network/pre_{model_file}_{regions}_{resarea}_l{ll}_{opts}.nc",
    threads: 1
    resources:
        mem=4000,
    script:
        "scripts/prepare_network.py"

rule solve_network:
    input: 
        network="networks/pre_{model_file}_{regions}_{resarea}_l{ll}_{opts}.nc",
        model_file="data/model_file.xlsx",
    output: "results/networks/solved_{model_file}_{regions}_{resarea}_l{ll}_{opts}.nc"
    shadow: "shallow"
    log:
        solver=normpath(
            "logs/solve_network/solved_{model_file}_{regions}_{resarea}_l{ll}_{opts}_solver.log"
        ),
        python="logs/solve_network/solved_{model_file}_{regions}_{resarea}_l{ll}_{opts}_python.log",
        memory="logs/solve_network/solved_{model_file}_{regions}_{resarea}_l{ll}_{opts}_memory.log",
    benchmark: "benchmarks/solve_network/solved_{model_file}_{regions}_{resarea}_l{ll}_{opts}"
    threads: 15
    resources: mem_mb=40000 # for electricity only
    script: "scripts/solve_network.py"


rule plot_network:
    input:
        network='results/version-0.6/networks/solved_{model_file}_{regions}_{resarea}_l{ll}_{opts}.nc',
        model_file="data/model_file.xlsx",
        supply_regions='data/supply_regions/supply_regions_{regions}.shp',
        resarea = lambda w: "data/bundle/" + config['data']['resarea'][w.resarea]
    output:
        only_map='results/version-0.6/plots/{model_file}_{regions}_{resarea}_l{ll}_{opts}_{attr}.{ext}',
        ext='results/version-0.6/plots/{model_file}_{regions}_{resarea}_l{ll}_{opts}_{attr}_ext.{ext}',
    log: 'logs/plot_network/{model_file}_{regions}_{resarea}_l{ll}_{opts}_{attr}.{ext}.log'
    script: "scripts/plot_network.py"
