Manual installation
===================

The manual installation gives you full control over Azure resource creation.
You provision the resources yourself and configure Cycas by filling in the
``.env`` file by hand. Use this if you already have existing Azure resources
or prefer to manage infrastructure through your own tooling (Terraform, Bicep,
Azure Portal, etc.).

.. contents:: Contents
   :local:
   :depth: 1


Prerequisites
-------------

**Python 3.13**

.. code-block:: bash

   sudo add-apt-repository ppa:deadsnakes/ppa
   sudo apt update
   sudo apt install python3.13
   sudo apt install python3.13-venv

**Azure CLI** - used for authentication:

.. code-block:: bash

   curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash

**Azure permissions** - Owner access on the target resource group or Contributor combined with the User Access Administrator role (or
Owner on the subscription if you want to create a new resource group).

Clone the repository, create a virtual environment, and install Cycas:

.. code-block:: bash

   git clone https://github.com/nerium-security/cycas
   cd cycas
   python3.13 -m venv .venv
   source .venv/bin/activate
   pip install .

Log in to Azure:

.. code-block:: bash

   az login


Azure Data Explorer (ADX) setup guide
--------------------------------------

Our preference is to create a new resource group which allows for quick deletion of all resources as they are typically short-lived.

#. Create an Azure Data Explorer (ADX) cluster and select the workload size as ``Dev/test`` for testing purposes and small engagements. For the purpose of this manual we will use the GUI and not the CLI. At Nerium we have our internal platform that automates this setup with OpenTofu (the open-source variant of Terraform).

   .. image:: /_static/images/adx-setup-1.png

#. When the cluster is created, create the database and name it (for example) 'dfir'

   .. image:: /_static/images/adx-setup-2.png

#. Assign ``AllDatabasesAdmin`` permissions to the identity that will run Cycas (your user account or the processor's managed identity).

   .. image:: /_static/images/database-permissions.png


Azure Storage Account setup guide
----------------------------------

Cycas uses a single Storage Account for three purposes: tracking processing status (Table), receiving input ZIP files (Blob), and writing per-run status snapshots (Blob).

#. Create a Storage Account.

#. Create the following blob containers inside the storage account:

   - ``uploads`` - where triage ZIP files are placed for processing
   - ``status`` - where Cycas writes per-run status JSON blobs
   - ``config`` - where artifact definitions and configuration are stored

#. Create a Table named ``statusupdate`` (or your preferred name) for processing status tracking.

#. Assign the following roles to the identity running Cycas:

   - ``Storage Blob Data Contributor`` on the storage account

     .. image:: /_static/images/storage-table-contributor.png

   - ``Storage Table Data Contributor`` on the storage account

#. Create a Storage Queue named ``triagepackages`` (or your preferred name). The watcher places new ZIP files on this queue; the processor reads from it.

#. Assign ``Storage Queue Data Contributor`` on the storage account to the identity running Cycas.


Azure Key Vault setup (optional)
---------------------------------

These steps are only required if triage packages collected with Velociraptor are encrypted with a ZIP password. Cycas reads the password from Key Vault at processing time.

#. Create an Azure Key Vault with RBAC authorization enabled.

#. Add a secret named ``velociraptor-collection-password`` and set its value to the ZIP password.

   .. image:: /_static/images/keyvault-secret.png

#. Assign the following roles:

   - ``Key Vault Secrets Officer`` to the person or identity responsible for managing the password (allows setting and updating the secret).
   - ``Key Vault Secrets User`` to the identity running the Cycas processor (allows reading the secret at processing time).

   .. image:: /_static/images/keyvault-permissions.png


Azure Functions setup (optional)
---------------------------------

This section is only required when running Cycas as Azure Functions (the recommended mode for automated, cloud-based processing). Skip this if you are running Cycas locally.

Cycas uses two Function Apps:

- **Watcher** - scans blob storage for new ZIP files and queues them for processing
- **Processor** - picks up queued items, extracts and ingests them into ADX

For each Function App you need:

#. A dedicated **Storage Account** (used internally by the Function App runtime).

#. A **Flex Consumption App Service Plan** (one per Function App).

#. A **Function App** with a system-assigned managed identity enabled.

#. **Application Insights** resource for monitoring (can be shared between both apps).

Once both Function Apps are created, assign the required roles to their managed identities.
See :doc:`/reference/permissions` for the full list of roles required by each component.

Deploy the function code using the Azure Functions Core Tools:

.. code-block:: bash

   func azure functionapp publish <watcher-app-name> --python
   func azure functionapp publish <processor-app-name> --python


Configure env variables
------------------------

First copy the ``.env_example`` to ``.env``:

.. code-block:: bash

   cp .env_example .env

Open ``.env`` in a text editor and fill in the values for your environment.

.. note::

   Boolean values must be set to ``true`` or ``false`` (lowercase).
   Leave a variable empty (``VARIABLE=``) rather than removing it - all
   keys must be present for the configuration to load correctly.

**ADX:**

.. code-block:: ini

   ADX_CLUSTER_ENABLED=true
   ADX_CLUSTER_URI=https://<cluster-name>.<region>.kusto.windows.net
   ADX_CLUSTER_INGESTION_URI=https://ingest-<cluster-name>.<region>.kusto.windows.net
   ADX_DATABASE_NAME=dfir

**Blob storage** (input ZIPs via SAS URL):

.. code-block:: ini

   BLOB_STORAGEACCOUNT_SAS_ENABLED=true
   BLOB_STORAGEACCOUNT_SAS=https://<resourcename>.blob.core.windows.net/uploads?sp=racwl&st=...&sig=<OBFUSCATED>

**Status tracking** (mandatory):

.. code-block:: ini

   BLOB_LOGTABLE_ENABLED=true
   BLOB_LOGTABLE_URI=https://<storageaccount>.table.core.windows.net
   BLOB_LOGTABLE_NAME=statusupdate

**Key Vault** (optional, only required when ZIPs are password-protected):

.. code-block:: ini

   KEYVAULT_ENABLED=true
   KEYVAULT_URL=https://<vault-name>.vault.azure.net
   KEYVAULT_PASSWORDLOCATION=velociraptor-collection-password


Running Cycas
-------------

Once ``.env`` is configured and the minimum resources are configured (Blob and ADX), 
you can run it locally via below command.

The intented and preferred way is to use Azure Functions.

.. code-block:: bash

   python3 scripts/pipeline/zip2adx.py

To start the web dashboard:

.. code-block:: bash

   python3 gunicorn --bind=0.0.0.0:8000 --timeout 600 webapp.app:app

The dashboard is available at ``http://localhost:8000``.
