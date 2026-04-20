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



## License
See LICENSE for details.