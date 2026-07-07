Collecting triage packages with Velociraptor
============================================

Our preferred approach at Nerium is to deploy a Velociraptor client across all
endpoints and initiate collections from the server using a custom artifact. This
artifact uploads triage packages directly to Azure Blob Storage, bypassing the
Velociraptor server entirely.

We strongly recommend this direct-to-blob approach for large-scale collections.
It eliminates the extra transfer step, avoids storing large amounts of data on
the Velociraptor server, and is significantly more reliable. As in our experience,
the server consistently struggles to handle more than ~20 simultaneous triage
uploads.


Direct upload from Velociraptor client to Azure Blob Storage 
------------------------------------------------------------

The custom artifact ``Custom.Windows.Triage.Targets.AzureUpload`` extends
``Windows.Triage.Targets``: it zips the collected files on the endpoint and
uploads the ZIP directly to Azure Blob Storage via a SAS URL. The Velociraptor
server is *not* involved in the file transfer.

Importing Windows.Triage.Targets
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- ``Windows.Triage.Targets`` must be available on your Velociraptor server. Import
  it from `triage.velocidex.com <https://triage.velocidex.com/docs/windows.triage.targets/>`_
  before adding the custom artifact.
- A write-only SAS URL for the blob container that Cycas is watching.


Importing the artefact for direct upload to Azure Blob Storage
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

1. In the Velociraptor GUI, go to **View Artifacts → Add an Artifact**.
2. Upload ``velociraptor/definitions/Custom.Windows.Triage.Targets.AzureUpload.yaml``
   from the Cycas repository.

Find the SAS URL
~~~~~~~~~~~~~~~~

The SAS URL for the Cycas blob container is required as input fo the artefact
 ``Custom.Windows.Triage.Targets.AzureUpload.yaml`` The URL can be found in the 
 kevyault that is provisioned using the ``easy_install.py`` script. 

   .. image:: /_static/images/sas_url.png


Running a collection
~~~~~~~~~~~~~~~~~~~~

1. Create a new hunt and select the artifact
   ``Custom.Windows.Triage.Targets.AzureUpload``.
2. Fill in the parameters:

   +-----------------------+-------------------------------------------------------+
   | Parameter             | Description                                           |
   +=======================+=======================================================+
   | **SasURL**            | The write-only SAS URL generated above.               |
   +-----------------------+-------------------------------------------------------+
   | **UploadPrefix**      | Optional folder prefix inside the container,          |
   |                       | e.g. ``2024-IR/``. Leave blank to upload to the root. |
   +-----------------------+-------------------------------------------------------+
   | **ZipPassword**       | Optional password for the ZIP. Leave blank for no     |
   |                       | encryption. If set, configure Cycas with the matching |
   |                       | password (Key Vault or ``.env``).                     |
   +-----------------------+-------------------------------------------------------+
   | **HighLevelTargets**  | Select the artifact groups to collect, e.g.           |
   | **Targets**           | ``_KapeTriage``.                                      |
   +-----------------------+-------------------------------------------------------+
   | **Devices**           | Drive letters to search (default: ``C:``).            |
   +-----------------------+-------------------------------------------------------+

3. Start the collection. Each endpoint collects, zips, and uploads its triage package
   directly to blob. Once the ZIP lands in blob storage, Cycas picks it up
   automatically.
