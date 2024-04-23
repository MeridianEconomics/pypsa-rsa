.. pypsa-za documentation master file, created by
   sphinx-quickstart on Fri Jan  6 10:47:23 2023.
   You can adapt this file completely to your liking, but it should at least
   contain the root `toctree` directive.

PyPSA-RSA: An open Optimisation Model of the South African Power System
=======================================================================

PyPSA-RSA is an open model dataset of the South African power system at three spatial resolutions namely;

- ``1-supply``: A single node for the entire South Africa.
- ``11-supply``: 11 nodes based on the `Eskom Generation Connection Capacity Assessment of the 2024 Transmission Network (GCCA – 2024)  <https://www.eskom.co.za/eskom-divisions/tx/gcca/>`_ regions.
- ``27-supply``: 27 nodes based on Eskom 27 supply regions.

.. image:: img/1-supply.png
   :width: 500
   :align: center
   :alt: 1-supply

.. image:: img/11-supply.png
   :width: 500
   :align: center
   :alt: 9-supply

.. image:: img/27-supply.png
   :width: 500
   :align: center
   :alt: 27-supply

PyPSA-RSA is a high temporal resolution multi-horizon expansion planning model for a least cost optimised transition path to 
a decarbonised power sector over the coming decades for South Africa.

PyPSA-RSA makes use of freely available and open data which encourages the open exchange of model data developments and eases the comparison of model results. 
It provides a full, automated software pipeline to assemble the load-flow-ready model from the original datasets, which enables easy replacement and 
improvement of the individual parts.

PyPSA-RSA is designed to be imported into the open toolbox `PyPSA <https://pypsa.org/>`_ for which `documentation <https://pypsa.readthedocs.io/en/latest/index.html>`_ is available as well.

This project is currently maintained by `Meridian Economics <https://meridianeconomics.co.za/>`_. Previous versions were developed within the Energy Centre 
at the `Council for Scientific and Industrial Research (CSIR) <https://www.csir.co.za/>`_ as part of the `CoNDyNet project <https://fias.institute/en/projects/condynet/>`_, which is supported by the 
`German Federal Ministry of Education and Research <https://www.bmbf.de/bmbf/en/home/home_node.html>`_ under grant no. 03SF0472C.

The model is currently under development and has been validated for the single node (`1-supply`), for more information on the capability of the moel please see the :ref:`release-notes`. 

.. note::
  Credits to `PyPSA-Eur <https://github.com/PyPSA/pypsa-eur>`_ and `PyPSA-Meets-Earth <https://github.com/pypsa-meets-earth/pypsa-earth>`_ developers for the initial drafting of the documentation here reported and adapted where necessary

=============
Documentation
=============

**Getting Started**

* :doc:`introduction`
* :doc:`installation`
* :doc:`workflow`
* :doc:`tutorials`
* :doc:`data_workflow`

.. toctree::
   :hidden:
   :maxdepth: 2
   :caption: Getting Started

   introduction
   installation
   workflow  
   tutorials
   data_workflow    

**Configuration**

* :doc:`wildcards`
* :doc:`configuration`
* :doc:`costs`

.. toctree::
   :hidden:
   :maxdepth: 2
   :caption: Configuration

   wildcards
   configuration
   costs

**API**

* :doc:`api_reference`

.. toctree::
   :hidden:
   :maxdepth: 2
   :caption: API

   api_reference


**Help and References**

* :doc:`release_notes`
* :doc:`how_to_contribute`

.. toctree::
   :hidden:
   :maxdepth: 2
   :caption: Project Info

   release_notes
   how_to_contribute
