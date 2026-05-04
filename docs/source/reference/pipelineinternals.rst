Pipeline internals
==================

.. contents:: On this page
   :local:
   :depth: 2


Overview
--------

The pipeline runs in five phases. Each ZIP file passes through all of them
independently, in parallel with other ZIPs.

**Discover** — The pipeline connects to the configured input source (local
folder, blob, SAS, or SFTP) and lists all ZIP files that match the configured
prefix and suffix filters. Each discovered ZIP is checked against Table Storage
to determine whether it has already been processed. ZIPs that are new or
eligible for retry are passed to the next phase.

**Download** — For remote sources, the ZIP is fetched from blob storage or
SFTP and saved to a local directory. For the local folder source this phase is
skipped, as the files are already on disk. The status of each ZIP is updated
in Table Storage throughout so that interrupted runs can be safely resumed.

**Extract** — The ZIP is unpacked into a working directory. Encrypted archives
have their password retrieved automatically from Azure Key Vault. Velociraptor
triage packages often contain a nested inner archive (``data.zip``), which is
detected and extracted automatically.

**Post-process** *(optional)* — If the ZIP contains raw forensic artifacts
and Velociraptor post-processing is enabled, Velociraptor is invoked to run
the configured artifact queries against the archive contents. The hostname is
identified from the SYSTEM registry hive inside the ZIP. Each artifact query
produces a structured output file (JSONL, JSON, or CSV) that is passed
directly to the ingestion phase.

**Ingest** — JSON and JSONL files — whether produced by Velociraptor or
already present in the ZIP — are uploaded to Azure Data Explorer. Each record
is enriched with the hostname before upload. Target tables are created or
updated automatically based on the file contents. A detailed status record is
written to ADX on completion, and an optional webhook notification is sent.


Phases
------

Discover
~~~~~~~~

Before any ZIP files are processed, ``init()`` runs once per session to
create required local directories, generate a unique session ID, and
authenticate all service managers (ADX, Table Storage, Key Vault, Blob, SFTP)
based on the enabled flags in ``.env``. Only managers required for the
selected input source are authenticated — disabled services are bypassed.

The pipeline then connects to the configured source and lists all files
matching ``VAR_ZIPFILE_PREFIX`` and ``VAR_ZIPFILE_SUFFIX``. Each discovered
ZIP is written to Table Storage with status ``NEW`` on first sight. ZIPs that
already have a ``PROCESSING`` or ``FINISHED`` status entry are skipped,
preventing double-processing when the script is run multiple times against the
same source. Set ``VAR_MAX_RETRY`` to allow previously failed ZIPs to be
retried automatically.


Download
~~~~~~~~

For remote sources (blob, SAS, SFTP), the ZIP is fetched and saved to
``VAR_DOWNLOAD_DIRECTORY``. The Table Storage status transitions from
``DOWNLOADING`` at the start to ``DOWNLOADED`` on success or
``DOWNLOADFAILED`` on error. A failed download causes the ZIP to be skipped
for the remainder of the run.

For the local folder source no download occurs — the file is already on disk
and the pipeline moves directly to extraction.

.. list-table::
   :widths: 20 80
   :header-rows: 1

   * - Status
     - When it is set
   * - ``NEW``
     - ZIP is first discovered. Entry is created in Table Storage.
   * - ``PROCESSING``
     - Processing has started and the ZIP has passed the deduplication check.
   * - ``DOWNLOADING``
     - Download from the remote source has begun.
   * - ``DOWNLOADED``
     - Download completed successfully.
   * - ``DOWNLOADFAILED``
     - Download encountered an error. The ZIP is skipped for this run.
   * - ``FINISHED``
     - All extraction, post-processing, and ingestion steps completed.


Extract
~~~~~~~

The ZIP is extracted to a subdirectory of ``VAR_UNZIP_DIRECTORY``. Two
scenarios are handled:

- **Unencrypted ZIP** — extracted directly.
- **Encrypted ZIP** — the password is read from Key Vault when
  ``KEYVAULT_ENABLED=true``. If Key Vault is disabled, the password is
  read from the ``ZIP_PASSWORD`` environment variable or prompted
  interactively.

Velociraptor triage packages commonly contain a nested ``data.zip`` inside
the outer archive. This inner ZIP is detected and extracted automatically.


Post-process
~~~~~~~~~~~~

When ``VELOCIRAPTOR_ENABLED=true``, the orchestrator checks whether the ZIP
contains raw artifacts by looking for an ``uploads/`` directory inside the
archive. If raw artifacts are found, the following steps run:

- The Velociraptor binary is downloaded from ``VELOCIRAPTOR_URL`` if it is
  not already present at ``VELOCIRAPTOR_BINARY``.
- A remapping file is generated (see below) so that Velociraptor can read
  directly from the ZIP without fully unpacking it.
- The hostname is extracted from the ``SYSTEM`` registry hive inside the ZIP
  via the remapping file.
- Artifact queries are loaded from ``VELOCIRAPTOR_ARTIFACTSLIST`` and
  filtered to the configured subset (``essential`` or ``full``).
- Each artifact query is executed in sequence. Output is written to disk in
  the format specified by ``VELOCIRAPTOR_OUTPUTFORMAT`` (``jsonl``, ``json``,
  or ``csv``).
- Each output file is immediately queued for ADX ingestion, then deleted from
  disk.

If the ZIP does not contain raw artifacts, this phase is skipped and the
pipeline proceeds directly to ingestion.


Ingest
~~~~~~

Every JSON and JSONL file — whether produced by Velociraptor or already
present in the ZIP — is processed in turn:

- Files matching patterns in ``VAR_LOCATION_IGNORELIST`` are recorded in the
  status output but not uploaded.
- Each file is extracted to ``VAR_UNZIP_DIRECTORY``, enriched with a
  ``hostname`` column derived from the ZIP filename, and queued for ADX
  ingestion.
- Files larger than 6 GB are automatically split into smaller chunks before
  upload to stay within the ADX ingestion limit.
- After ingestion is initiated, the local file is deleted from disk.

The ADX table name is derived from the filename of each JSON/JSONL file.
If the table does not yet exist, it is created automatically with a schema
inferred from the file contents. If it already exists, the schema is merged.

Once all files have been ingested, the status is updated to ``FINISHED`` in
Table Storage. A detailed status record — including per-file upload results,
post-processing metadata, hostname, and timing — is written to the ``_status``
table in ADX. If ``VAR_WEBHOOK_URL`` is set, a summary message is posted to
that URL.


Concurrency
-----------

The pipeline uses a ``ThreadPoolExecutor`` to process multiple ZIP files in
parallel. The pool size is controlled by ``VAR_LOCALDEVICE_CONCURRENCY``.
Each ZIP runs in its own thread and is fully isolated — a failure in one ZIP
does not affect others. Set the value to ``1`` to process ZIPs sequentially,
which is useful for debugging.

.. _status_tracking:

Status tracking
---------------

Each ZIP file is assigned a status in Azure Storage Account table throughout its
lifecycle. The status is updated at every phase transition, allowing
interrupted runs to be safely resumed and providing a full audit trail
of what happened to each file.

.. note::
   Default name of the Azure Storage Account table is ``statusupdate`` and can be configured in the .env file with ``BLOB_LOGTABLE_NAME``)


You can inspect the status here:

   .. image:: /_static/images/statustracking.png
      :target: /_static/images/statustracking.png

The following statuses are supported:

.. list-table::
   :widths: 20 80
   :header-rows: 1

   * - Status
     - Description
   * - ``NEW``
     - The ZIP has been discovered for the first time. An entry is created
       in Table Storage. The file has not yet been picked up for processing.
   * - ``QUEUED``
     - The extracted file has been queued for processing with Azure Functions.
   * - ``UNQUEUED``
     - The ZIP has been removed from the queue and picked up for processing.
   * - ``PROCESSING``
     - Processing has started and the ZIP has passed the deduplication check.
   * - ``DOWNLOADING``
     - The ZIP is being fetched from the remote source (blob, SAS, or SFTP).
   * - ``DOWNLOADED``
     - The download completed successfully. The file is ready for extraction.
   * - ``DOWNLOADFAILED``
     - The download encountered an error. The ZIP is skipped for the
       remainder of the run. Set ``VAR_MAX_RETRY`` to allow it to be
       retried in a subsequent run.
   * - ``UPLOADING``
     - The file is actively being uploaded to ADX.
   * - ``FAILED``
     - A general failure occurred during processing. Check the run log
       for details. Set ``VAR_MAX_RETRY`` to allow the ZIP to be retried.
   * - ``FINISHED``
     - All extraction, post-processing, and ingestion steps completed
       successfully. A detailed status record has been written to the
       ``_status`` table in ADX.


A typical successful run transitions through the following statuses:
 
.. code-block:: none
 
   NEW → QUEUED → UNQUEUED → PROCESSING → DOWNLOADING → DOWNLOADED → UPLOADING → FINISHED


.. _adx_statustables:

ADX status tables
-----------------
 
After processing of a ZIP file, three tables are updated in Azure Data Explorer providing
a detailed breakdown of what happened during the run. All three tables share
the ``uploadid`` and ``zipfile`` columns, making them straightforward to join
in KQL.
 
.. list-table::
   :widths: 25 60
   :header-rows: 1
 
   * - Table
     - Description
   * - ``_status_summary``
     - High-level overview of each archive — session, source, file size,
       timing, and encryption. Use this as the starting point when
       investigating a specific ZIP.
   * - ``_status_uploads``
     - Tracks every file considered for upload — what was uploaded to ADX,
       what was post-processed by Velociraptor, and what was ignored and why.
   * - ``_status_postprocessing``
     - Tracks each artifact query that was run — success, duration, and any
       errors returned. Use this to diagnose Velociraptor failures.

An example of the ``_status_uploads`` is depicted here. It shows which files in the zip are uploaded, wich are ignored (and why), etc:

   .. image:: /_static/images/status_uploads.png
      :target: /_static/images/status_uploads.png


Module structure
----------------
 
Pipeline
~~~~~~~~
 
.. list-table::
   :widths: 40 60
   :header-rows: 1
 
   * - Module
     - Responsibility
   * - ``core/pipeline/runner.py``
     - Entry point. Discovers ZIPs and manages the thread pool.
   * - ``core/pipeline/orchestrator.py``
     - Coordinates the full lifecycle of a single ZIP file.
 
Utils
~~~~~
 
.. list-table::
   :widths: 40 60
   :header-rows: 1
 
   * - Module
     - Responsibility
   * - ``core/utils/auth.py``
     - Authenticates and initialises all service managers.
   * - ``core/utils/config.py``
     - Loads configuration from the ``.env`` file.
   * - ``core/utils/zip.py``
     - ZIP discovery, extraction, encryption, and ignore list handling.
   * - ``core/utils/postprocess.py``
     - Velociraptor binary management, remapping, and artifact execution.
   * - ``core/utils/status.py``
     - Table Storage status reads and writes.
   * - ``core/utils/summary.py``
     - Results dictionary construction and summary output.
   * - ``core/utils/files.py``
     - File size checks, JSONL splitting, and local file deletion.
   * - ``core/utils/misc.py``
     - Shared helpers: download flag resolution and webhook dispatch.
   * - ``core/utils/log.py``
     - Logging setup and session ID generation.
 
Managers
~~~~~~~~
 
.. list-table::
   :widths: 40 60
   :header-rows: 1
 
   * - Module
     - Responsibility
   * - ``core/manager/adx.py``
     - ADX table management and file ingestion.
   * - ``core/manager/blob.py``
     - Azure Blob Storage download.
   * - ``core/manager/sas.py``
     - Blob Storage access via SAS token.
   * - ``core/manager/sftp.py``
     - SFTP file listing and download.
   * - ``core/manager/keyvault.py``
     - Key Vault secret retrieval.
   * - ``core/manager/table.py``
     - Azure Table Storage read and write.
   * - ``core/manager/azure.py``
     - Azure credential and subscription management.
   * - ``core/manager/queue.py``
     - Azure Queue Storage (Azure Functions variant only).