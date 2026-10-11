# SPDX-FileCopyrightText:  PyPSA-RSA Authors
# SPDX-License-Identifier: MIT

"""Regression tests for scenario-definition loading.

Two scenario sets ship in this repository and they use different schemas. The
loader has to accept both, because a scenario set that predates a field means
the feature was not part of that study -- not that the field should be invented.
These tests pin that contract: current-schema workbooks load unchanged, legacy
workbooks load with their renamed fields, and a genuinely missing physical input
still raises rather than being silently defaulted.
"""

import sys
import types
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

# The loader logs; it does not need a live snakemake object.
sys.modules.setdefault("snakemake", types.ModuleType("snakemake"))

from _helpers import load_scenario_setup  # noqa: E402

SCENARIOS = Path(__file__).resolve().parents[1] / "scenarios"
COAL = SCENARIOS / "Coal_Flexibilisation" / "scenarios_to_run.xlsx"
MEIRP = SCENARIOS / "ME IRP 2024" / "scenarios_to_run.xlsx"


def _legacy_workbook(path):
    """A workbook in the pre-rename schema: `wildcard` id, no `solver`."""
    frame = pd.DataFrame(
        {
            "wildcard": ["L1", "L2"],
            "run_scenario": [True, False],
            "simulation_years": ["2025,2030,2035", "2025,2030,2035"],
            "co2_constraints": ["none", "none"],
            "extendable_techs": ["IRP", "IRP"],
        }
    )
    frame.to_excel(path, sheet_name="scenario_definition", index=False)
    return path


@pytest.fixture
def legacy_file(tmp_path):
    return _legacy_workbook(tmp_path / "legacy.xlsx")


def test_current_schema_loads_unchanged():
    """The shipped Coal_Flexibilisation workbook must behave exactly as before."""
    setup = load_scenario_setup(COAL, "S1")

    assert setup.name == "S1"
    assert setup["solver"] == "highs"
    assert setup["carbon_constraints"] == "none"
    assert bool(setup["unit_committment"]) is True


def test_legacy_identifier_column_is_accepted(legacy_file):
    """`wildcard` is the legacy spelling of the scenario id column."""
    setup = load_scenario_setup(legacy_file, "L1", include_run_only=False)

    assert setup.name == "L1"
    # `run_scenario` stays a column instead of being consumed as the index.
    assert "run_scenario" in setup.index


def test_legacy_field_names_are_renamed(legacy_file):
    """Renaming on load keeps the scripts reading one vocabulary."""
    setup = load_scenario_setup(legacy_file, "L1", include_run_only=False)

    assert setup["carbon_constraints"] == "none"
    assert setup["extendable_active"] == "IRP"


def test_missing_boolean_fields_default_to_off(legacy_file):
    """A field absent from a legacy workbook means the feature was not studied."""
    setup = load_scenario_setup(legacy_file, "L1", include_run_only=False)

    assert bool(setup["unit_committment"]) is False
    assert bool(setup["endogenous_coal_decom"]) is False
    assert setup["phased_decom"] == "none"


def test_missing_physical_input_is_not_defaulted(legacy_file):
    """Emissions, fuel prices and weather years must fail loudly, not default.

    Inventing a value for a physical input changes results silently, so only
    fields whose absence unambiguously means "feature off" carry a default.
    """
    setup = load_scenario_setup(legacy_file, "L1", include_run_only=False)

    for absent in ("weather", "fixed_fuel_prices", "fixed_emissions"):
        with pytest.raises(KeyError):
            setup[absent]


def test_capacity_expansion_years_is_derived_from_the_horizon(legacy_file):
    """`build_topology` reads its first four characters; no workbook defines it."""
    setup = load_scenario_setup(legacy_file, "L1", include_run_only=False)

    assert setup["capacity_expansion_years"] == "2025"


def test_run_only_filter_still_selects_the_enabled_rows(legacy_file):
    """The default path returns only rows with run_scenario set."""
    setup = load_scenario_setup(legacy_file, "L1")

    assert setup.name == "L1"
    with pytest.raises(KeyError):
        load_scenario_setup(legacy_file, "L2")


def test_me_irp_workbook_loads_on_the_legacy_path():
    """The shipped ME IRP 2024 set must load rather than raise.

    Every row is run_scenario=False, so the run-only filter returns nothing;
    the legacy path is what a re-enabled row would take.
    """
    setup = load_scenario_setup(MEIRP, "IRP_REF_CI", include_run_only=False)

    assert setup.name == "IRP_REF_CI"
    assert setup["carbon_constraints"] == "none"
    assert setup["extendable_active"] == "IRP"
    assert "run_scenario" in setup.index
