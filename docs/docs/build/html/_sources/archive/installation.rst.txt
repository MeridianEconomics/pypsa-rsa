.. _installation:

##########################################
Installation
##########################################

The subsequently described installation steps are demonstrated as shell commands, where the path before the % sign denotes the directory in which the commands following the % should be entered.

Clone the Repository
====================


First of all, clone the `PyPSA-ZA repository <https://github.com/PyPSA/pypsa-za>`_ using the version control system ``git``.
The path to the directory into which the ``git repository`` is cloned, must **not** have any spaces!
If you do not have ``git`` installed, follow installation instructions `here <https://git-scm.com/book/en/v2/Getting-Started-Installing-Git>`_.

.. code-block:: bash

    /some/other/path % cd /some/path/without/spaces
    /some/path/without/spaces % git clone https://github.com/pypsa-za.git

.. _deps:

Install Python Dependencies
===========================

PyPSA-ZA relies on a set of other Python packages to function.
We recommend using the package manager and environment management system ``conda`` to install them.
Install `miniconda <https://docs.conda.io/en/latest/miniconda.html>`_, which is a mini version of `Anaconda <https://www.anaconda.com/>`_ 
that includes only ``conda`` and its dependencies or make sure ``conda`` is already installed on your system.
For instructions for your operating system follow the ``conda`` `installation guide <https://docs.conda.io/projects/conda/en/latest/user-guide/install/>`_.

The python package requirements are curated in the `envs/environment.yaml <https://github.com/PyPSA/pypsa-za/blob/master/environment.yaml>`_ file.

The environment can be installed and activated using

.. code-block:: bash 

    .../pypsa-za % conda env create -f environment.yaml
    .../pypsa-za % conda activate pypsa-za

Note that activation is local to the currently open shell!
After opening a new terminal window, one needs to reissue the second command!

.. note::
    If you have troubles with a slow ``conda`` installation, we recommend to install
    `mamba <https://github.com/QuantStack/mamba>`_ as a fast drop-in replacement via

    .. code-block:: bash

        conda install -c conda-forge mamba

    and then install the environment with

    .. code-block:: bash

        mamba env create -f envs/environment.yaml

Download Data Dependencies
==========================
For ease of installation and reproduction we provide a bundle pypsa-za-bundle.7z with the necessary data files.
To obtain and unpack the data bundle in the data folder

.. code-block:: bash

    .../data % wget "https://vfs.fias.science/d/f204668ef2/files/?dl=1&p=/pypsa-za-bundle.7z"   
    .../data % 7z x pypsa-za-bundle.7z

Install a Solver
================

PyPSA passes the PyPSA-ZA network model to an external solver for performing a total annual system cost minimization with optimal power flow.
PyPSA is known to work with the free software

- `Ipopt <https://coin-or.github.io/Ipopt/INSTALL.html>`_
- `Cbc <https://projects.coin-or.org/Cbc#DownloadandInstall>`_
- `GLPK <https://www.gnu.org/software/glpk/>`_ (`WinGLKP <http://winglpk.sourceforge.net/>`_)
- `HiGHS <https://highs.dev/>`_

For installation instructions of these solvers for your operating system, follow the links above.
Commercial solvers such as Gurobi and CPLEX currently significantly outperform open-source solvers for large-scale problems.
It might be the case that you can only retrieve solutions by using a commercial solver.

.. note::
    The rules :mod:`cluster_network` and :mod:`simplify_network` solve a quadratic optimisation problem for clustering.
    The open-source solvers Cbc and GlPK cannot handle this. A fallback to Ipopt is implemented in this case, but requires
    also Ipopt to be installed. For an open-source solver setup install in your ``conda`` environment on OSX/Linux

    .. code:: bash

        conda activate pypsa-za
        conda install -c conda-forge ipopt coincbc

    and on Windows

    .. code:: bash

        conda activate pypsa-za
        conda install -c conda-forge ipopt glpk

.. warning::
    On Windows, new versions of ``ipopt`` have caused problems. Consider downgrading to version 3.11.1.

.. _defaultconfig:

Set Up the Default Configuration
================================

PyPSA-ZA has several configuration options that must be specified in a ``config.yaml`` file located in the root directory.
An example configuration ``config.default.yaml`` is maintained in the repository.
More details on the configuration options are in :ref:`config`.

Before first use, create a ``config.yaml`` by copying the example.

.. code:: bash

    .../pypsa-za % cp config.default.yaml config.yaml

Users are advised to regularly check their own ``config.yaml`` against changes in the ``config.default.yaml``
when pulling a new version from the remote repository.
