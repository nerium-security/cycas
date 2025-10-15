## todo:
- Fix log entry updates
- instead of a file upload, upload with dataframe, is there a big speed difference?
- does it work when a zip file is uploaded without a password?
- to storage table, add the user that initiated it?
- more friendly icannotauthenticate error
- send webhook
- ignore path 'uploads' in zip folder
- Syntactic sugar:
    - overal dit of niet in een functie: self, host: str, username: str, port: int, ssh_key_str: str, )
    -  Overal authenticate checken: if not self.sftp:
                                        raise Exception('SFTP connection not established. Call authenticate() first.')
- re-upload files files works. But how to configure a retry if it hang
- build a new azure function that checks if a job is running longer then x minutes
- add computer name as column when uploading
- add queue to storage account if not exists (variable BLOB_QUEUE)
    - - Maybe auto-create queue 'triagepackages' when it does not exist?
- let a job fail after x minutes, and configure re-run count and a maximum
- verbose info per zip file and place it somewhere
- size of zip package in log entry
- upload nested json as HashServiceExe_SHA256 instead of HashServiceExe.SHA256
- What if a json has a size larger then 6442450944 bytes as that is the maximum. Maximum is 6gb: https://learn.microsoft.com/en-us/kusto/api/get-started/app-queued-ingestion?view=azure-data-explorer&utm_source=chatgpt.com&tabs=app%2Cpython
- Why does it crash when there are no permissions?
- how to ensure a failed file is uploaded again?
- what to do with licensing?
- use cases for testing: large file, 


# use cases

Options:
- Post-process using Azure Functions
- Post-process artefacts locally


# Instructions for deploying it in production
- Create ADX 
    - create a database named 'ir'. This is set via the variable ADX_DATABASE_NAME in .env
    - provide current user with permissions AllDatabaseAdmin
- Create blob storage
    - Add permissions to current user: Storage Queue Data Contributor
    - Add permissions to current user: Storage Table Data Contributor
    - Add permissions to current user: Storage Blob Data Contributor
    - Create the blob container 'triagepackages'. You can change this by setting the BLOB_CONTAINER_INPUT variable in .env 
    - Create a queue 'triagepackages'. You can change this with the BLOB_QUEUE_NAME variable in .env
- Create azure keyvault
    - Add permissions to current user: Key Vault Secrets user
    - Add a secret named 'velo-password', and set the password of the zip triage packege
- add all urls to .env config file
    - ADX_CLUSTER_URI
    - ADX_CLUSTER_INGESTION_URI 
    - BLOB_LOGTABLE_URI
    - BLOB_STORAGEACCOUNT_URI
    - BLOB_QUEUE_URL
    - KEYVAULT_URL 
- Install sharedlib (pip install ./sharedlib)
- Create azure function app: watcher
    - add system assigned identity: key vault secrets user, storage queue data message sender, storage table data contributor
- Create azure function app: processor
    - add system assigned identity: key vault secrets user, storage queue data message processor

# Instructions for development infrastructure
- pip install -e ./sharedlib
- cd azurefunctions/watcher
- ln -s ../../sharedlib/  ./sharedlib
- ln -s ../../.env .env
- cd azurefunctions/processor 
- ln -s ../../sharedlib/  ./sharedlib
- ln -s ../../.ignorelist.json  ./.ignorelist.json
- ln -s ../../.env .env