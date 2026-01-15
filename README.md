# Azure Data Explorer Ingestor

This Python-based solution that uploads forensic artefacts to Azure Data Explorer (ADX). It supports artefacts collected with Velociraptor, wether these are raw artefacts that require post-postprocessing or that are already post-processed to JSON format. 

## Use cases

When forensic artefacts are collected using Velociraptor

- Run on Windows/Linux device for post-process raw forensic artefacts to json(l) and csv using Velociraptor
- Pipeline for ingesting forensic artefacts at scale to automatically ingest zip files with forensic artefacts from Blob or SFTP storage using Azure Functions


## drawing 


## Supported inputsources

- Blob storage using azure authentication
- Blob storage using SAS tokens
- SFTP storage
- local folder


## Instructions for local post-processing data



## Instruction for pushing data to azure data explorer
- Create ADX 
    - create a database named 'ir'. This is set via the variable ADX_DATABASE_NAME in .env
    - provide a user with permissions AllDatabaseAdmin
- add all urls to .env config file
    - ADX_CLUSTER_URI
    - ADX_CLUSTER_INGESTION_URI 
    - BLOB_LOGTABLE_URI
    - BLOB_STORAGEACCOUNT_URI
    - BLOB_QUEUE_URL
    - KEYVAULT_URL 
- Install sharedlib (pip install ./sharedlib)

## Instructions for running the pipeline in azure functions


- Create blob storage
    - Add permissions to a user: Storage Queue Data Contributor
    - Add permissions to a user: Storage Table Data Contributor
    - Add permissions to a user: Storage Blob Data Contributor
    - Create the blob container 'triagepackages'. You can change this by setting the BLOB_CONTAINER_INPUT variable in .env 
    - Create a queue 'triagepackages'. You can change this with the BLOB_QUEUE_NAME variable in .env
- Create azure keyvault
    - Add permissions to a user: Key Vault Secrets user
    - Add a secret named 'velo-password', and set the password of the zip triage packege

Optional: When you want to use Azure Functions:

- Create azure function app: watcher
    - add system assigned identity: key vault secrets user, storage queue data message sender, storage table data contributor
- Create azure function app: processor
    - add system assigned identity: key vault secrets user, storage queue data message processor

## Instructions for development infrastructure
- pip install -e ./core
- cd azurefunctions/watcher
- ln -s ../../core/  ./core
- ln -s ../../.env .env
- cd azurefunctions/processor 
- ln -s ../../core/  ./core
- ln -s ../../.ignorelist.json  ./.ignorelist.json
- ln -s ../../.env .env