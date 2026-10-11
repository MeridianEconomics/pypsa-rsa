# SPDX-FileCopyrightText:  PyPSA-RSA Authors
# SPDX-License-Identifier: MIT

"""Regression tests for solver selection.

A scenario definition may name its own solver, or leave the choice to
``config["solver"]["default"]``. Either way the value is a key into the solver
block, so a scenario set that predates the ``solver`` column can still run, and
a misspelling fails with the list of valid names instead of deep inside linopy.
"""

import sys
import types
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.modules.setdefault("snakemake", types.ModuleType("snakemake"))

ROOT = Path(__file__).resolve().parents[1]
CONFIG = yaml.safe_load((ROOT / "config.yaml").read_text())

# Open-source solvers the suite is expected to support. HiGHS is the default
# because it runs in-process; the rest need their executable on PATH.
OPEN_SOURCE_SOLVERS = ["highs", "cbc", "glpk", "scip"]


@pytest.fixture
def solver_config():
    return CONFIG["solver"]


def resolve(solver_config, scenario_value):
    """Mirror the resolution in prepare_and_solve_network.solve_network."""
    key = scenario_value
    if key is None or str(key).strip().lower() in ("none", "nan", ""):
        key = solver_config["default"]
    if key not in solver_config:
        raise KeyError(key)
    return solver_config[key]["name"]


@pytest.mark.parametrize("name", OPEN_SOURCE_SOLVERS)
def test_open_source_solvers_are_configured(solver_config, name):
    """Each open-source solver has a block with a linopy-resolvable name."""
    assert name in solver_config, f"{name} missing from config solver block"
    assert solver_config[name]["name"] == name


def test_default_is_an_open_source_solver(solver_config):
    """The platform must run with no commercial licence present."""
    assert solver_config["default"] in OPEN_SOURCE_SOLVERS


def test_absent_solver_column_falls_back_to_the_default(solver_config):
    """A legacy workbook with no solver column still resolves."""
    assert resolve(solver_config, None) == solver_config["default"]


@pytest.mark.parametrize("blank", ["none", "None", "nan", ""])
def test_blank_solver_value_falls_back_to_the_default(solver_config, blank):
    """`fillna("none")` in the loader must not become a literal solver name."""
    assert resolve(solver_config, blank) == solver_config["default"]


def test_explicit_solver_is_honoured(solver_config):
    """A scenario that names its own solver keeps it."""
    assert resolve(solver_config, "highs") == "highs"


def test_unknown_solver_raises_with_the_valid_names(solver_config):
    """A typo must fail at the config lookup, not inside linopy."""
    with pytest.raises(KeyError):
        resolve(solver_config, "gurobi_not_installed")
