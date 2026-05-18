FAQ
===

.. contents:: Questions
   :local:
   :depth: 1
   :backlinks: none


What operating systems are supported?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
 
Cycas is supported to run on Linux. Although it would probably work on
Windows and macOS as well, but we have not tested this.

Cycas also supports post-processing raw artifacts collected from 
Windows endpoints, there is currently no support for post-processing
artifacts from other endpoints like macOS or Linux.

When raw artifacts are already post-processed to JSONL with Velociraptor
, whether this is from a Windows, macOS, or Linux device, it is supported.


Which artifacts are supported for post-processing?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

See :doc:`../reference/supportedartifacts`

Can I skip certain files from being ingested to Azure Data Explorer (ADX)?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Yes. Place a ``.ignorelist.json`` file in the root folder of Cycas.
It supports two keys:
 
- ``ignorelist`` — exact filenames or paths to skip (URL-encode ``/`` as ``%2F``
  in artifact paths)
- ``ignorepattern`` — glob-style patterns such as ``uploads/*``
 
Example:
 
.. code-block:: json
 
    {
      "ignorelist": [
        "log.json",
        "uploads.json",
        "Windows.KapeFiles.Targets%2FUploads.json"
      ],
      "ignorepattern": [
        "uploads/*"
      ]
    }

How to add artifacts for post-processing with Velocriaptor?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Artifacts are defined in ``Velociraptor/artifacts/velociraptor_artifacts.json``.
The file contains three lists:

- ``essential`` — a minimal set of artifacts collected in all cases
- ``full`` — the complete artifact set for a thorough investigation
- ``skip`` — artifacts to explicitly exclude from collection

Example:

.. code-block:: json

    {
      "essential": [
        "Custom.Windows.Registry.UserAssist()",
        "Custom.Windows.Sys.Users()"
      ],
      "full": [
        "Windows.Forensics.SRUM()",
        "Windows.Forensics.Prefetch()"
      ],
      "skip": [
        "Generic.Forensic.SQLiteHunter()"
      ]
    }

To add a new artifact, append its Velociraptor artifact name (including
parentheses and any parameters) to either the ``essential`` or ``full`` list.
To prevent an artifact from being collected, add it to the ``skip`` list.

If you want to add a custom artifact, add the Velociraptor artifact file to the folder as well: ``/velociraptor/definitions/``

What if I only want to post-process raw artifacts? And not ingest to ADX?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

You can use the standalone script we've built. See :doc:`../guides/postprocessingraw`.
Another solution is to use the pipeline script ``/scripts/pipeline/zip2adx.py`` and set the `.env` variable to `ADX_CLUSTER_ENABLED=false`. 
The advantage is that you can use automated tracking of which files are already processed.


How can I view that (detailed) status of how processing a ZIP went?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Detailed status information is found in Azure Data Explorer (ADX), see: :ref:`adx_statustables`

Cycas uses a table in Azure Storage Account that tracks if a ZIP is already processed or not: :ref:`status_tracking`

