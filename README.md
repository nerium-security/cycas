# cycas

Cycas is a is a Python pipeline for post-processing raw forensic artefacts collected with [Velociraptor](https://www.velocidex.com/golang/velociraptor/). It optionally ingests data into [Azure Data Explorer (ADX)](https://azure.microsoft.com/en-us/products/data-explorer). 

## Features

- Post-process raw Velociraptor artefacts (MFT, EVTX etc) into CSV, JSON, or JSONL format
- Ingest forensic artefacts at scale from Blob storage, SFTP, SAS token URLs, or a local folder
- Ingests the data into Azure Data Explorer (ADX) for quick analysis with Kusto Query Language (KQL)
- Encrypted ZIP support via Azure Key Vault or environment variables
- Deduplication and status tracking via Azure Table Storage
- Concurrent processing of multiple ZIP files

## Visualisation

Idea:
1) Collect raw artefacts from endpoints using Velocirpator -> upload to blob
2) post-process and upload to adx

## Supported Input Sources
 
| Source | Description |
|---|---|
| `blob` | Azure Blob Storage with managed identity or key-based auth |
| `sas` | Azure Blob Storage with SAS token |
| `sftp` | SFTP server |
| `localfolder` | Local directory on the machine running the script |

## How collected ZIPs are processed offline

Velociraptor artifacts are designed to run against a live Windows system. To run them against a collected ZIP containing raw evidence instead, Cycas generates a remapping file, which is a YAML configuration that tells Velociraptor how to treat the ZIP's contents as if they were a live machine.

The remapping file is dynamically generated against each ZIP file using the YAML configuration that is found here: [Custom.Generic.Utils.ZipRemap.yaml](velociraptor/artifacts/Custom.Generic.Utils.ZipRemap.yaml)


### Supported Artefacts
 
All Velociraptor artifacts that currently can be used by Cycas to post-process raw evidence are the following:

| Artefact | Description |
|---|---|
| `Windows.NTFS.MFT` | Master File Table — full filesystem metadata |
| `Windows.Forensics.Usn` | USN Journal ($UsnJrnl) — filesystem change history |
| `Windows.Sys.AppcompatShims` | Application compatibility shims |
| `Windows.Forensics.RecentApps` | Recently accessed files and applications from the registry |
| `Windows.Forensics.UserAccessLogs` | User Access Logs (UAL) — remote access and logon history |
| `Windows.Forensics.Shellbags` | Shellbags — folder browsing history |
| `Windows.Detection.Amcache` | Amcache.hve — file execution and installation history |
| `Windows.System.Powershell.PSReadline` | PowerShell command history |
| `Windows.System.TaskScheduler` | Scheduled tasks |
| `Windows.Forensics.RecycleBin` | Recycle Bin contents and metadata |
| `Windows.EventLogs.Evtx` | Windows Event Logs (all .evtx files) |
| `Windows.Registry.AppCompatCache` | AppCompatCache (Shimcache) — program execution evidence |
| `Windows.Forensics.Prefetch` | Prefetch files — execution evidence |
| `Windows.Sys.Programs` | Installed programs |
| `Windows.Forensics.JumpLists` | Jump Lists — recently/frequently accessed files per application |
| `Windows.Forensics.Timeline` | Windows Timeline / Activity history |
| `Windows.Forensics.SRUM` | System Resource Usage Monitor — process, network, and energy usage history |
 
The following Velociraptor artifacts work as well, but it required (in some cases small) customisation to the artifact indicated by the 'custom'.

| Artefact | Description |
|---|---|
| `Custom.Windows.Forensics.Bam` | Background Activity Moderator — records of executed binaries |
| `Custom.Windows.Forensics.SAM` | SAM database — local account and group information |
| `Custom.Windows.Registry.Interfaces` | Network interface registry keys |
| `Custom.Windows.Registry.NTUser` | NTUser.dat registry hive contents |
| `Custom.Windows.Registry.RDP` | RDP-related registry keys (MRU, client connection history) |
| `Custom.Windows.Registry.RecentDocs` | Recently opened documents from the registry |
| `Custom.Windows.Registry.UserAssist` | Programs run by each user, with run counts, from UserAssist registry keys |
| `Custom.Windows.Sys.Users` | Local user accounts |


## Setting up the automated ingestion pipeline

This section walks you through the setup of 


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

## Instruction for ad-hoc analysis

For ad-hoc analysis on a local system, you can run the following script to post-process ZIPs and output the results:

`python3 scripts/standalone/process_with_velo.py -i /opt/collected_evidence/Collection-DPP000458-2026-02-03T20_35_20Z.zip`

By-default it outputs the results to the same folder 

- Windows.EventLogs.Evtx.csv
- Windows.NTFS.MFT.csv
- Custom.Windows.Registry.UserAssist.csv
- etc


### Building a standalone executable

To distribute the post-processing script as a standalone executable (no Python required):

```bash
pip install pyinstaller
pyinstaller --onefile --collect-all core scripts/standalone/process_with_velo.py
```

The executable will be available in the `dist/` folder.



## License
See LICENSE for details.