# cycs

Cycis is a is a Python pipeline for post-processing raw forensic artefacts collected with [Velociraptor](https://www.velocidex.com/golang/velociraptor/). It optionally ingests data into [Azure Data Explorer (ADX)](https://azure.microsoft.com/en-us/products/data-explorer). 

## Features

- Post-process raw Velociraptor artefacts into CSV, JSON, or JSONL format
- Ingest forensic artefacts at scale from Blob storage, SFTP, SAS token URLs, or a local folder
- Ingests the data into Azure Data Explorer (ADX) for quick analysis with Kusto Query Language (KQL)
- Automated pipeline using Azure Functions (watcher + processor)
- Encrypted ZIP support via Azure Key Vault or environment variables
- Deduplication and status tracking via Azure Table Storage
- Webhook notifications on pipeline events
- Concurrent processing of multiple ZIP files

## Supported Input Sources
 
| Source | Description |
|---|---|
| `blob` | Azure Blob Storage with managed identity or key-based auth |
| `sas` | Azure Blob Storage with SAS token |
| `sftp` | SFTP server |
| `localfolder` | Local directory on the machine running the script |
 
## Supported Artefacts
 
All artefacts are Windows-specific. The artefact set is split into two tiers, selectable via the `-e` / `--essentials` flag in the standalone script or the `VELOCIRAPTOR_ARTIFACTSLIST` config.
 
### Essential (minimal, fast)
 
| Artefact | Description |
|---|---|
| `Custom.Windows.Registry.UserAssist` | Programs run by each user, with run counts, from UserAssist registry keys |
| `Custom.Windows.Sys.Users` | Local user accounts |
 
### Full (default)
 
| Artefact | Description |
|---|---|
| `Windows.Forensics.SRUM` | System Resource Usage Monitor — process, network, and energy usage history |
| `Windows.Forensics.Usn` | USN Journal ($UsnJrnl) — filesystem change history |
| `Windows.Sys.AppcompatShims` | Application compatibility shims |
| `Custom.Windows.Forensics.Bam` | Background Activity Moderator — records of executed binaries |
| `Windows.Forensics.RecentApps` | Recently accessed files and applications from the registry |
| `Windows.Forensics.UserAccessLogs` | User Access Logs (UAL) — remote access and logon history |
| `Custom.Windows.Registry.NTUser` | NTUser.dat registry hive contents |
| `Custom.Windows.Registry.RDP` | RDP-related registry keys (MRU, client connection history) |
| `Custom.Windows.Registry.RecentDocs` | Recently opened documents from the registry |
| `Windows.Forensics.Shellbags` | Shellbags — folder browsing history |
| `Windows.Detection.Amcache` | Amcache.hve — file execution and installation history |
| `Custom.Windows.Forensics.SAM` | SAM database — local account and group information |
| `Windows.System.Powershell.PSReadline` | PowerShell command history |
| `Windows.System.TaskScheduler` | Scheduled tasks |
| `Windows.Forensics.RecycleBin` | Recycle Bin contents and metadata |
| `Windows.EventLogs.Evtx` | Windows Event Logs (all .evtx files) |
| `Custom.Windows.Registry.Interfaces` | Network interface registry keys |
| `Windows.Registry.AppCompatCache` | AppCompatCache (Shimcache) — program execution evidence |
| `Windows.Forensics.Prefetch` | Prefetch files — execution evidence |
| `Windows.Sys.Programs` | Installed programs |
| `Windows.NTFS.MFT` | Master File Table — full filesystem metadata |
| `Windows.Forensics.JumpLists` | Jump Lists — recently/frequently accessed files per application |
| `Windows.Forensics.Timeline` | Windows Timeline / Activity history |
 
The essential artefacts are a subset of the full list. `Generic.Forensic.SQLiteHunter` is explicitly skipped.
 

## Use cases

When forensic artefacts are collected using Velociraptor

- Run on Windows/Linux device for post-process raw forensic artefacts to json(l) and csv using Velociraptor
- Pipeline for ingesting forensic artefacts at scale to automatically ingest zip files with forensic artefacts from Blob or SFTP storage using Azure Functions
- From velociraptor server to azure data explorer

## Building a standalone executable

To distribute the post-processing script as a standalone executable (no Python required):

```bash
pip install pyinstaller
pyinstaller --onefile --collect-all core scripts/standalone/process_with_velo.py
```

The executable will be available in the `dist/` folder.


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
- pip install -e .
- cd azurefunctions/watcher
- ln -s ../../core/  ./core
- ln -s ../../.env .env
- cd azurefunctions/processor 
- ln -s ../../core/  ./core
- ln -s ../../.ignorelist.json  ./.ignorelist.json
- ln -s ../../.env .env


## License
See LICENSE for details.