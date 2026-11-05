# Cycas

Cycas is a Digital Forensics & Incident Response (DFIR) platform that turns the
forensic data you collect with [Velociraptor](https://github.com/Velocidex/velociraptor)
into a queryable [Azure Data Explorer (ADX)](https://azure.microsoft.com/nl-nl/products/data-explorer)
database. It is built by the incident responders at Nerium, for incident responders.

With every engagement you spin up a fresh environment. A single command spins 
up the entire platform in Azure in about 15 minutes. The work of provisioning a 
cloud forensic platform is done for you, including  ready-to-deploy Velociraptor
client installers for Windows, Linux and macOS. 

## Compute that scales with the incident

An incident can mean five endpoints or five thousand. Cycas is designed so the
compute follows the size of the case:

- **Ingestion scales out horizontally.** The processing runs on Azure Functions
  (Flex Consumption), which fan out across many instances under load - Cycas
  comfortably processes 500+ triage packages concurrently - and scale back to
  nothing when the queue is empty.
- **Pay-per-use, disposable.** No up-front SIEM licence. A whole engagement can
  live in a single resource group and be deleted when it is closed - you pay for
  what the incident actually needs.

## Collect triage at scale with one hunt

Cycas provisions your Velociraptor server with a prepared hunt that collects a
full triage package (containing raw forensic artefacts) from every in-scope endpoint. 
Each endpoint zips its collection and uploads it **directly to Blob Storage, bypassing 
the Velociraptor server** - so collection scales past the server's  limit to hundreds 
of hosts at once. Cycas then picks up each package and ingests it into ADX automatically.

Launch it from the Velociraptor GUI and the evidence flows in on its own - see
[Start a mass collection](https://cycas.readthedocs.io/en/latest/guides/masscollection.html)
in the docs.

## One command, a full forensic platform

`python easy_install.py` provisions and connects everything Cycas needs. Behind
the scenes it sets up:

- An **ADX cluster and database**, sized to the engagement.
- A **storage account** - the blob input container, a processing queue, and a
  status table.
- **Two Azure Functions** (a watcher and a processor) on a plan that scales out
  horizontally, with Application Insights for monitoring.
- A **Key Vault** for the collection ZIP password and service
  credentials.
- A **Velociraptor server** on a VM with a static IP, with
  **prebuilt client installers** (standard and obfuscated Windows MSIs, Linux
  deb/rpm, macOS) and a set of **prepared hunts** ready to launch.
- A **status dashboard**.
- **Offline Velociraptor collectors** for Windows, Linux and macOS, uploaded to
  the input container.

Connection strings, role assignments, managed identities and secrets are all
configured automatically, and progress is saved so an interrupted run can
resume.

## Dashboard

Cycas includes a dashboard so you can track ingestion status at a
glance.

![Ingestion Dashboard](docs/source/_static/images/webapp_status.png)

## Getting started

We've made it easy to get started. Find the instructions in our documentation to
run `easy_install.py`: [Getting Started](https://cycas.readthedocs.io/en/latest/gettingstarted/installation.html)

## License

See LICENSE for details.
