Installation
============




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