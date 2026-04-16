Usage
=====
 
Cycas provides four pipeline scripts for ingesting forensic triage packages (in ZIP format)
into Azure Data Explorer (ADX). Each script targets a different source for
the zip files but shares the same underlying pipeline.
 
.. contents:: On this page
   :local:
   :depth: 2
 

Scripts
-------

.. list-table::
   :widths: 22 28 16
   :header-rows: 1

   * - Script
     - Source
     - Use when
   * - ``sas2adx.py``
     - Azure Blob Storage via SAS token
     - ZIP files are in blob and you have a SAS token
   * - ``blob2adx.py``
     - Azure Blob Storage via RBAC authentication
     - ZIP files are in blob have access via service principal or managed identity
   * - ``sftp2adx.py``
     - Remote SFTP server
     - Triage packages are delivered to an SFTP drop zone
   * - ``localfolder2adx.py``
     - Local directory
     - Packages are already on disk, or you are testing the pipeline locally


