Cycas
=====

Cycas is a Digital Forensics & Incident Response (DFIR) platform that turns
forensic data collected with `Velociraptor <https://github.com/Velocidex/velociraptor>`_
into a queryable `Azure Data Explorer (ADX) <https://azure.microsoft.com/products/data-explorer>`_
database. It is built by the incident responders at Nerium, for incident responders.

A single command provisions the whole platform in Azure - an ADX cluster,
horizontally scaling Azure Functions, a Velociraptor server with ready-to-deploy
client installers and prepared hunts, offline collectors, and a status dashboard
- so you can focus on the investigation instead of the infrastructure.

New here? Start with :doc:`/gettingstarted/installation`.

.. toctree::
   :maxdepth: 2
   :hidden:
   :caption: Getting started

   /gettingstarted/installation

   /gettingstarted/usage

.. toctree::
   :maxdepth: 2
   :hidden:
   :caption: Guides

   /guides/masscollection

   /guides/analyse-results-adx

   /guides/velociraptor-client-obfuscation

   /guides/postprocessingraw

.. toctree::
   :maxdepth: 2
   :hidden:
   :caption: Reference

   /reference/architecture-azure

   /reference/pipelineinternals

   /reference/supportedartifacts

   /reference/permissions

.. toctree::
   :maxdepth: 2
   :hidden:
   :caption: Project

   /project/faq

   /project/aboutus