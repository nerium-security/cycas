# Upload hunt results to Azure Blob Storage (SAS)

This VQL snippet packages the results of a Velociraptor hunt into a single
ZIP file and uploads it directly to an Azure Blob Storage container using a
SAS URL. 

## VQL

```vql
LET ZipPassword <= ""
LET sas_url <= ""
LET hunt_id <= ""

LET completions = SELECT *,
         create_hunt_download(hunt_id=hunt_id ,password=ZipPassword, wait=TRUE, only_combined=TRUE) AS FlowDownload
         FROM scope()

SELECT upload_azure(
  file=FlowDownload,
  accessor="fs",
  sas_url=sas_url,
  name=format(format="Hunt %v %v.zip",args=[hunt_id, timestamp(epoch=now())])
  ) AS Upload
FROM completions
```

## Instructions

1. Open the Velociraptor GUI and create a new notebook (or add a new cell to
   an existing one), then paste the VQL above.
2. Fill in the variables at the top of the query:
   - `hunt_id` - the ID of the hunt whose results you want to export
     (e.g. `H.C1234567`). You can find this in the URL or on the **Hunt
     Manager** page for the hunt.
   - `sas_url` - the full Azure Blob Storage SAS URL for the **container**
     that should receive the ZIP, including the SAS token query string
     (e.g. `https://<account>.blob.core.windows.net/<container>?<sas-token>`).
     This should be a container configured as a `sas` input source in Cycas.
   - `ZipPassword` - optional password to encrypt the resulting ZIP. Leave
     empty (`""`) for no password. If you set one, make sure Cycas is
     configured with the matching password (Key Vault or environment
     variable) so it can open the ZIP.
3. Run the query.
6. Once the ZIP is in Blob Storage, Cycas (running with the `sas` input
   source pointed at the same container) will pick it up automatically for
   post-processing and ingestion.

> **Note:** the SAS token must have **write** and **list**
> permissions on the container, and an expiry far enough in the future to
> cover the upload of large hunts.