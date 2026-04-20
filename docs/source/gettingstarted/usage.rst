Usage
=====
 
Cycas provides a single pipeline script, ``zip2adx.py``, that ingests
forensic triage packages into Azure Data Explorer (ADX). The input source
is controlled entirely through the ``.env`` file — no command-line arguments
are needed.

.. code-block:: bash

  python scripts/pipeline/zip2adx.py
 
.. contents:: On this page
   :local:
   :depth: 2


Input sources
-------------
 
Enable exactly one input source in your ``.env`` file by setting its
``_ENABLED`` flag to ``true`` and leaving the others as ``false``.
 
.. list-table::
   :widths: 22 32 46
   :header-rows: 1
 
   * - Source
     - Enable flag
     - Use when
   * - Local folder
     - ``VAR_LOCALFOLDER_ENABLED``
     - ZIP files are already present on disk or a mounted directory.
   * - SAS
     - ``BLOB_STORAGEACCOUNT_SAS_ENABLED``
     - You have a scoped SAS URL to an Azure Blob container.
   * - Blob
     - ``BLOB_STORAGEACCOUNT_ENABLED``
     - You have full storage account access via Azure identity or managed identity.
   * - SFTP
     - ``SFTP_ENABLED``
     - ZIP files are delivered to a remote SFTP server.
  
 
Pipeline flow
-------------
 
Regardless of the input source, every ZIP file goes through the same steps:
 
1. **Discover** — list ZIP files from the configured source, filtered by ``VAR_ZIPFILE_PREFIX`` and ``VAR_ZIPFILE_SUFFIX``.
2. **Download** — fetch each ZIP to ``VAR_DOWNLOAD_DIRECTORY`` (skipped for local folder).
3. **Extract** — decrypt and unpack the archive. The password is read from Key Vault when ``KEYVAULT_ENABLED=true``.
4. **Post-process** *(optional)* — when ``VELOCIRAPTOR_ENABLED=true`` and the ZIP contains raw artifacts, run them through Velociraptor to produce JSON/JSONL output.
5. **Ingest** — upload extracted JSON/JSONL files to ADX.
6. **Status** — write per-ZIP status updates to Azure Table Storage.
 
Processing is concurrent. Control parallelism with ``VAR_LOCALDEVICE_CONCURRENCY``.


