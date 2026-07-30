# Cycas

Cycas is a Digital Forensics & Incident Response (DFIR) pipeline that ingests data from [Velociraptor](https://github.com/Velocidex/velociraptor) into [Azure Data Explorer (ADX)](https://azure.microsoft.com/nl-nl/products/data-explorer). It's built by incident responders at Nerium, used internally on engagements for a couple of years, and now open source.

![Structured data in Azure Data Explorer](docs/source/_static/images/adx-results.png)

# Why Cycas

*Collect first, analyse later.* At the start of an incident you don't yet know what you're looking for, and the evidence is aging out by the minute. Velociraptor is excellent at that collection, and it's the tool we reach for on nearly every engagement. But it was never meant to store and query months of filesystem activity, and event logs for hundreds of hosts, all at once. It isn't a SIEM.

Cycas fills the two gaps:

- *Preserving evidence at scale.* The most valuable raw forensic artifact from every endpoint is captured at the start of the engagement and post-processed into structured data, so the original evidence is always there to go back to.
- *Analysing evidence at scale.* Everything lands in a single Azure Data Explorer (ADX) cluster, normalised into one schema and queryable across every host with Kusto Query Language (KQL) - instead of re-querying live endpoints one at a time.

# What Cycas ingests

Cycas takes in two kinds of data, and both end up in the same ADX cluster, queryable together:

- **Raw forensic artifacts, preserved at scale.** Triage packages are uploaded directly to Blob Storage or SFTP - straight from the endpoint, not routed through the Velociraptor server — and Cycas post-processes the raw artifacts (MFT, USN journal, event logs, and the rest) into structured data before ingesting them into ADX. 
- **Results of Velociraptor hunts.** Hunts you initiate from the Velociraptor server produce output that's already structured; Cycas ingests those results straight into ADX, so your live-response findings sit alongside the preserved artifacts.

## Features

- Ingests triage packages from Azure Blob Storage, SFTP, or a local folder.
- Easily processes 500+ packages (each over 1 GB) concurrently on Azure Functions.
- Post-process raw Velociraptor artifacts (MFT, EVTX etc) before ingesting them into ADX.
- Automatic ADX schema inference. No hand-maintained table definitions; schemas extend themselves as new fields appear.
- Tags every record with the hostname it came from, for cross-host hunting.
- Pay-per-use Azure resources. ADX pricing means no expensive SIEM licence up front, and a whole engagement can live in a disposable resource group.

## Dashboard

To make it user-friendly, we've built a dashboard so you can track the status. 

![Ingestion Dashboard](docs/source/_static/images/webapp_status.png)

## Getting started

We've made it easy to get started. Find the instruction in our documentation to run `easy_install.py`: [Getting Started](https://cycas.readthedocs.io/en/latest/gettingstarted/installation.html#prerequisites)


## Supported Input Sources
 
| Source | Description |
|---|---|
| `blob` | Azure Blob Storage with managed identity or key-based auth |
| `sas` | Azure Blob Storage with SAS token |
| `sftp` | SFTP server |
| `localfolder` | Local directory on the machine running the script |


### Supported Artifacts
 
All Velociraptor artifacts that currently can be used by Cycas to post-process raw evidence are listed below. The dashboard made it easy to configure more artifacts.

| Artifact | Description |
|---|---|
| `Windows.NTFS.MFT` | Master File Table: full filesystem metadata |
| `Windows.Forensics.Usn` | USN Journal ($UsnJrnl): filesystem change history |
| `Windows.Sys.AppcompatShims` | Application compatibility shims |
| `Windows.Forensics.RecentApps` | Recently accessed files and applications from the registry |
| `Windows.Forensics.UserAccessLogs` | User Access Logs (UAL): remote access and logon history |
| `Windows.Forensics.Shellbags` | Shellbags: folder browsing history |
| `Windows.Detection.Amcache` | Amcache.hve: file execution and installation history |
| `Windows.System.Powershell.PSReadline` | PowerShell command history |
| `Windows.System.TaskScheduler` | Scheduled tasks |
| `Windows.Forensics.RecycleBin` | Recycle Bin contents and metadata |
| `Windows.EventLogs.Evtx` | Windows Event Logs (all .evtx files) |
| `Windows.Registry.AppCompatCache` | AppCompatCache (Shimcache): program execution evidence |
| `Windows.Forensics.Prefetch` | Prefetch files: execution evidence |
| `Windows.Sys.Programs` | Installed programs |
| `Windows.Forensics.JumpLists` | Jump Lists: recently/frequently accessed files per application |
| `Windows.Forensics.Timeline` | Windows Timeline / Activity history |
| `Windows.Forensics.SRUM` | System Resource Usage Monitor: process, network, and energy usage history |
 
The following Velociraptor artifacts work as well, but it required (in some cases small) customisation to the artifact indicated by the 'custom'.

| Artifact | Description |
|---|---|
| `Custom.Windows.Forensics.Bam` | Background Activity Moderator: records of executed binaries |
| `Custom.Windows.Forensics.SAM` | SAM database: local account and group information |
| `Custom.Windows.Registry.Interfaces` | Network interface registry keys |
| `Custom.Windows.Registry.NTUser` | NTUser.dat registry hive contents |
| `Custom.Windows.Registry.RDP` | RDP-related registry keys (MRU, client connection history) |
| `Custom.Windows.Registry.RecentDocs` | Recently opened documents from the registry |
| `Custom.Windows.Registry.UserAssist` | Programs run by each user, with run counts, from UserAssist registry keys |
| `Custom.Windows.Sys.Users` | Local user accounts |



## License
See LICENSE for details.
