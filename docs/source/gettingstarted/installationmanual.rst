Manual installation
===================

The manual installation gives you full control over Azure resource creation.
You provision the resources yourself and configure Cycas by filling in the
``.env`` file by hand. Use this if you already have existing Azure resources
or prefer to manage infrastructure through your own tooling (Terraform, Bicep,
Azure Portal, etc.).


Azure Data Explorer (ADX) setup guide
-------------------------------------

Our preference is to create a new resource group which allows for quick deletion of all resources as they are typically short-lived.

#. Create an Azure Data Explorer (ADX) cluster and select the workload size as ``Dev/test`` for testing purposes and small engagements. For the purpose of this manual we will use the GUI and not the CLI. At Nerium we have our internal platform that automates this setup with OpenTofu (the open-source variant of Terraform). 

   .. image:: /_static/images/adx-setup-1.png

#. When the cluster is created, create the database and name it (for example) 'dfir'

   .. image:: /_static/images/adx-setup-2.png

#. Assign ``AllDatabasesAdmin`` permissions to the database that was created.

   .. image:: /_static/images/database-permissions.png


Azure Storage Account setup guide
---------------------------------------

To be able to track the status of which ZIP files are already processed, cycas uses a Storage Account table. 

#. Ceate a Storage Account

#. Add ``Storage Table Data Contributor`` permissions to the storage account 

   .. image:: /_static/images/storage-table-contributor.png



Azure Key Vault setup (optional)
--------------------------------

These steps are only required if you prefer to encrypt the with Velociraptor collected data with a password when it's zipped. Cycas reads the password of the ZIP 

#. Create a Azure Key Vault

#. Add the secret named ``velo-password``

   .. image:: /_static/images/keyvault-secret.png

#. Add the role ``Key Vault Secrets User``.

  .. image:: /_static/images/keyvault-permissions.png


Configure env variables
-----------------------

First copy the .env_example to .env:

   .. code-block:: bash
 
      cp .env_example .env
 
#. Open ``.env`` in a text editor and fill in the values for your environment. The sections below describe every variable.
 
.. note::
 
   Boolean values must be set to ``true`` or ``false`` (lowercase).
   Leave a variable empty (``VARIABLE=``) rather than removing it - all
   keys must be present for the configuration to load correctly.
 
Our prefered setup is to store the ZIP file in a storage account blob and from there upload it to Azure Data Explorer (ADX). 

To configure the script to use ADX:

 .. code-block:: ini
 
   ADX_CLUSTER_ENABLED=true
   ADX_CLUSTER_URI=https://<cluster-name>.<region>.kusto.windows.net
   ADX_CLUSTER_INGESTION_URI=https://ingest-<cluster-name>.<region>.kusto.windows.net
   ADX_DATABASE_NAME=dfir
 
To configure the script to locate the ZIP files in a Blob containing forensic packages:

 .. code-block:: ini
 
   BLOB_STORAGEACCOUNT_SAS_ENABLED=true
   BLOB_STORAGEACCOUNT_SAS=https://<resourcename>.blob.core.windows.net/uploads?sp=racwl&st=2026-04-01T07:45:25Z&se=2026-04-15T16:00:25Z&spr=https&sv=2024-11-04&sr=c&sig=<OBFUSCATED>

To configure the script to track the status (mandatory):

 .. code-block:: ini
 
   BLOB_LOGTABLE_ENABLED=true
   BLOB_LOGTABLE_URI=https://orangerosevelofiles.table.core.windows.net
   BLOB_LOGTABLE_NAME=statusupdate

To configure Azure keyvault (optional, only required when ZIP's are encrypted with a password):

 .. code-block:: ini
 
   KEYVAULT_ENABLED=true
   KEYVAULT_URL=https://<vault-name>.vault.azure.net
   KEYVAULT_PASSWORDLOCATION=velo-password

