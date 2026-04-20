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


Initialisation
--------------

Before any ZIP files are processed, ``init()`` runs once per session:

- Creates required local directories (log, unzip, local folder) if they do
  not already exist.
- Generates a unique session ID that is attached to all log entries for the run.
- Instantiates and authenticates all service managers (ADX, Table Storage,
  Key Vault, Blob, SFTP) based on the enabled flags in ``.env``.

Only the managers required for the selected input source are authenticated.
Disabled services are skipped and log a message confirming they were bypassed.


ZIP processing lifecycle
------------------------

Each ZIP file goes through the following stages inside ``run_zip_processor()``.

1. Status: NEW
~~~~~~~~~~~~~~

A log entry is written to Azure Table Storage when the ZIP is first seen.
If an entry already exists and ``VAR_MAX_RETRY`` has not been exceeded, the
ZIP is skipped and processing stops here. This prevents double-processing when
the script is run multiple times against the same source.

2. Status: DOWNLOADING
~~~~~~~~~~~~~~~~~~~~~~

For remote sources (blob, SAS, SFTP), the ZIP is downloaded to
``VAR_DOWNLOAD_DIRECTORY`` before extraction. The status is updated to
``DOWNLOADING`` at the start and transitions to either ``DOWNLOADED`` or
``DOWNLOADFAILED`` depending on the outcome. This step is skipped entirely
for the local folder source.

3. Extraction
~~~~~~~~~~~~~

The ZIP is extracted to a subdirectory of ``VAR_UNZIP_DIRECTORY``. Two
scenarios are handled:

- **Unencrypted ZIP** — extracted directly.
- **Encrypted ZIP** — the password is read from Key Vault when
  ``KEYVAULT_ENABLED=true``. If Key Vault is disabled, the password is
  read from the ``ZIP_PASSWORD`` environment variable or prompted
  interactively.

Velociraptor triage packages commonly contain a nested ``data.zip`` inside
the outer archive. This inner ZIP is detected and extracted automatically.

4. Velociraptor post-processing *(optional)*
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

When ``VELOCIRAPTOR_ENABLED=true``, the orchestrator checks whether the ZIP
contains raw artifacts by looking for an ``uploads/`` directory inside the
archive. If raw artifacts are found, the following steps run:

- The Velociraptor binary is downloaded from ``VELOCIRAPTOR_URL`` if it is
  not already present at ``VELOCIRAPTOR_BINARY``.
- A remapping file is generated that allows Velociraptor to read directly
  from the extracted ZIP without fully unpacking it.
- The hostname is extracted from the ``SYSTEM`` registry hive inside the ZIP
  via the remapping file.
- Artifact queries are loaded from ``VELOCIRAPTOR_ARTIFACTSLIST`` and
  filtered to the configured subset (``essential`` or ``full``).
- Each artifact query is executed in sequence. Output is written to disk in
  the format specified by ``VELOCIRAPTOR_OUTPUTFORMAT`` (``jsonl``, ``json``,
  or ``csv``).
- Each output file is immediately queued for ADX ingestion, then deleted from
  disk.

If the ZIP does not contain raw artifacts, this stage is skipped and the
pipeline proceeds directly to JSON extraction.

5. JSON extraction and ingestion
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Every JSON and JSONL file inside the extracted ZIP is processed in turn:

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

6. Status: FINISHED
~~~~~~~~~~~~~~~~~~~~

Once all files have been processed, the status is updated to ``FINISHED`` in
Table Storage. A detailed status record — including per-file upload results,
post-processing metadata, hostname, and timing — is written to the ``_status``
table in ADX. If ``VAR_WEBHOOK_URL`` is set, a summary message is posted to
that URL.


Status transitions
------------------

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

ZIPs already in ``PROCESSING`` or ``FINISHED`` state are skipped on subsequent
runs. Set ``VAR_MAX_RETRY`` to allow failed ZIPs to be retried.


Concurrency
-----------

``run_localdevice()`` uses a ``ThreadPoolExecutor`` to process multiple ZIP
files in parallel. The pool size is controlled by ``VAR_LOCALDEVICE_CONCURRENCY``.
Each ZIP runs in its own thread and is fully isolated — a failure in one ZIP
does not affect others. Set the value to ``1`` to process ZIPs sequentially,
which is useful for debugging.


Module structure
----------------

.. list-table::
   :widths: 40 60
   :header-rows: 1

   * - Module
     - Responsibility
   * - ``core/pipeline/runner.py``
     - Entry point. Discovers ZIPs and manages the thread pool.
   * - ``core/pipeline/orchestrator.py``
     - Coordinates the full lifecycle of a single ZIP file.
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
   * - ``core/manager/queue.py``
     - Azure Queue Storage (Azure Functions variant only).