# Azure Data Explorer Ingestor

The **Azure Data Explorer Ingestor (ADX Ingestor)** is a Python-based tool designed for automating the ingestion of Velociraptor data (either raw forensic artifacts collected via kapefile artifact or json files) into Explorer (ADX)**. It supports reading zip files (either password encrypted or not) from Blob storage, SFTP storage and localfolders. 

## Supported inputsources

Supported sources for zip files:
- Blob storage using azure authentication
- Blob storage using SAS tokens
- SFTP storage
- local folder


## Minimal instruction for running the pipeline locally
- Create ADX 
    - create a database named 'ir'. This is set via the variable ADX_DATABASE_NAME in .env
    - provide a user with permissions AllDatabaseAdmin
- Create blob storage
    - Add permissions to a user: Storage Queue Data Contributor
    - Add permissions to a user: Storage Table Data Contributor
    - Add permissions to a user: Storage Blob Data Contributor
    - Create the blob container 'triagepackages'. You can change this by setting the BLOB_CONTAINER_INPUT variable in .env 
    - Create a queue 'triagepackages'. You can change this with the BLOB_QUEUE_NAME variable in .env
- Create azure keyvault
    - Add permissions to a user: Key Vault Secrets user
    - Add a secret named 'velo-password', and set the password of the zip triage packege
- add all urls to .env config file
    - ADX_CLUSTER_URI
    - ADX_CLUSTER_INGESTION_URI 
    - BLOB_LOGTABLE_URI
    - BLOB_STORAGEACCOUNT_URI
    - BLOB_QUEUE_URL
    - KEYVAULT_URL 
- Install sharedlib (pip install ./sharedlib)


Optional: When you want to use Azure Functions:

- Create azure function app: watcher
    - add system assigned identity: key vault secrets user, storage queue data message sender, storage table data contributor
- Create azure function app: processor
    - add system assigned identity: key vault secrets user, storage queue data message processor

## Instructions for development infrastructure
- pip install -e ./sharedlib
- cd azurefunctions/watcher
- ln -s ../../sharedlib/  ./sharedlib
- ln -s ../../.env .env
- cd azurefunctions/processor 
- ln -s ../../sharedlib/  ./sharedlib
- ln -s ../../.ignorelist.json  ./.ignorelist.json
- ln -s ../../.env .env