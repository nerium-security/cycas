Permissions
===========

This page is a reference guide for the Azure RBAC roles required by each
Cycas component. All components authenticate using a managed identity.
Roles follow the principle of least privilege.

.. note::
   The :ref:`easy installation <easy-installation>` assigns all
   of these roles automatically. This page is intended for manual deployments
   and auditing existing setups.

.. contents:: On this page
   :local:
   :depth: 2


Web application
---------------

The web application reads and writes status entries and displays the ingestion
dashboard. Assign the following roles to the web app's managed identity.

Blob roles for the web application are scoped to individual containers, not
the full storage account.

.. list-table::
   :widths: 35 30 15 20
   :header-rows: 1

   * - Service
     - RBAC role
     - Scope
     - Purpose
   * - Azure Table Storage
     - Storage Table Data Contributor
     - Storage account
     - Read and update ZIP processing status entries.
   * - Azure Blob Storage
     - Storage Blob Data Reader
     - ``status`` container
     - Read ZIP status JSON files.
   * - Azure Blob Storage
     - Storage Blob Data Contributor
     - ``config`` container
     - Read and write the configuration container.


Azure Function: Watcher
-----------------------

The watcher runs on a timer (every minute by default) and discovers new ZIP files in the
configured source, then enqueues them for processing. Assign the following
roles to the watcher's managed identity.

.. list-table::
   :widths: 35 35 30
   :header-rows: 1

   * - Service
     - RBAC role
     - Purpose
   * - Azure Blob Storage
     - Storage Blob Data Contributor
     - List and read ZIP files from the data storage account.
   * - Azure Queue Storage
     - Storage Queue Data Contributor
     - Enqueue discovered ZIP files for the processor.
   * - Azure Table Storage
     - Storage Table Data Contributor
     - Read and update ZIP processing status entries.
   * - Azure Key Vault
     - Key Vault Secrets User
     - Retrieve ZIP decryption passwords (when ``KEYVAULT_ENABLED=true``).


Azure Function: Processor
--------------------------

The processor is triggered by queue messages and runs the full pipeline
(download, extract, post-process, ingest) for each ZIP file. Assign the
following roles to the processor's managed identity.

.. list-table::
   :widths: 35 35 30
   :header-rows: 1

   * - Service
     - RBAC role
     - Purpose
   * - Azure Blob Storage
     - Storage Blob Data Contributor
     - Download ZIP files for processing.
   * - Azure Queue Storage
     - Storage Queue Data Contributor
     - Dequeue messages, update visibility, and delete processed messages.
   * - Azure Table Storage
     - Storage Table Data Contributor
     - Read and update ZIP processing status entries.
   * - Azure Data Explorer (ADX)
     - AllDatabasesAdmin
     - Ingest artifacts and create or update tables as needed.
   * - Azure Key Vault
     - Key Vault Secrets User
     - Retrieve ZIP decryption passwords (when ``KEYVAULT_ENABLED=true``).
