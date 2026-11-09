Analyse results with Azure Data Explorer
========================================

Cycas ingests every collected artifact into the Azure Data Explorer (ADX)
cluster provisioned during setup, where you query it with the Kusto Query
Language (KQL).

.. image:: /_static/images/adx-results.png

Open the query interface
------------------------

Analyse the data from the ADX web UI at `dataexplorer.azure.com
<https://dataexplorer.azure.com>`_ (or from your cluster's **Query** blade in
the Azure Portal). Add your cluster, then select the database - ``dfir`` by
default.

How the data is organised
-------------------------

- **One table per artifact type.** Each artifact is ingested into its own table,
  named with the ``Cycas_`` prefix (``ADX_TABLE_PREFIX`` in ``.env``) - for
  example ``Cycas_MFT`` or ``Cycas_Amcache``. The table schema is inferred
  automatically and extends itself as new fields appear.
- **Every record is tagged** with three columns so you can trace it back and
  pivot across artifacts:

  - ``cycas_hostname`` - the endpoint the artifact came from.
  - ``cycas_sourcezip`` - the triage ZIP it was extracted from.
  - ``cycas_uploadid`` - the collection/upload it belongs to.

To see which tables exist in the database, run:

.. code-block:: text

   .show tables

Example queries
---------------

List everything collected from a single host (replace the table and hostname):

.. code-block:: kusto

   Cycas_MFT
   | where cycas_hostname == "WORKSTATION01"
   | take 100

Count how many records each host contributed to a table - a quick way to spot
hosts with missing or partial collections:

.. code-block:: kusto

   Cycas_Amcache
   | summarize records = count() by cycas_hostname
   | sort by records desc

Narrow a search to a time window and a specific host:

.. code-block:: kusto

   Cycas_MFT
   | where cycas_hostname == "WORKSTATION01"
   | where Timestamp >= datetime(2026-11-01) and Timestamp < datetime(2026-11-05)

.. tip::

   Because every table shares the ``cycas_hostname`` column, you can correlate
   across artifact types - for example join file-system activity against
   event-log entries for the same host - to build a timeline across the estate.

For the full list of artifacts Cycas processes and the tables they produce, see
:doc:`/reference/supportedartifacts`.
