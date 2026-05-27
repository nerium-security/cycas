.. cycas documentation master file, created by
   sphinx-quickstart on Thu Apr  9 10:43:16 2026.
   You can adapt this file completely to your liking, but it should at least
   contain the root `toctree` directive.

cycas documentation
===================

Cycas is a Digital Forensics & Incident Response (DFIR) pipeline for post-processing and ingesting forensic artifacts collected with `Velociraptor <https://github.com/Velocidex/velociraptor>`_. into `Azure Data Explorer (ADX) <https://azure.microsoft.com/nl-nl/products/data-explorer>`_. It handles raw artifacts like MFT, EVTX, Amcache, Prefetch, and many others, converting them to CSV/JSON/JSONL before ingesting them into ADX for analysis with Kusto Query Langue (KQL).

It concurrently processes the data (ZIP files collected to Blob storage or SFTP) at scale using Azure Functions that run horizontally. 

.. figure:: /_static/images/overview_cycas.png
   :alt: Ingestion Dashboard
   :align: center
   :target: /_static/images/overview_cycas.png

.. toctree::
   :maxdepth: 2
   :hidden:
   :caption: Getting started

   /gettingstarted/installationeasy

   /gettingstarted/installationmanual

   /gettingstarted/usage

.. toctree::
   :maxdepth: 2
   :hidden:
   :caption: Guides

   /guides/collectingtriagedata

   /guides/postprocessingraw

.. toctree::
   :maxdepth: 2
   :hidden:
   :caption: Reference

   /reference/pipelineinternals

   /reference/supportedartifacts

   /reference/permissions

.. toctree::
   :maxdepth: 2
   :hidden:
   :caption: Project

   /project/faq

   /project/aboutus