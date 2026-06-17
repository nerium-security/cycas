Ingesting Velociraptor hunting results into Azure Data Explorer
===============================================================

The :doc:`triage collection guide </guides/collectingtriagedata>` covers uploading
packages directly from endpoints to blob, but what about hunt results that are
already sitting on the Velociraptor server? How is that ingested into Azure Data
Explorer?

``Server.Utils.BackupAzure`` answers this by shipping flow outputs directly to 
Azure Blob Storage. Once in blob, Cycas picks it up automatically and ingests 
it into Azure Data Explorer, giving you a single place to query all investigation 
data with KQL.

Finding the SAS URL
-------------------

The SAS URL for the Cycas blob container can be found in the Key Vault
provisioned by ``easy_install.py``.

   .. image:: /_static/images/sas_url.png


Import Server.Utils.BackupAzure
--------------------------------

``Server.Utils.BackupAzure`` is not a built-in artifact, import it using the 
following instructions:

1. In the Velociraptor GUI, go to **View Artifacts → Add an Artifact**.
2. Add the following artifact: `server.utils.backupazure <https://docs.velociraptor.app/exchange/artifacts/pages/server.utils.backupazure/>`_ 


Configure Server.Utils.BackupAzure
----------------------------------

1. In the Velociraptor GUI, go to **Server Events → Update server monitoring table**.
2. Search for ``Server.Utils.BackupAzure`` and select it.
3. Fill in the parameters:

   .. image:: /_static/images/server_event_monitoring.png

4. Launch it. 