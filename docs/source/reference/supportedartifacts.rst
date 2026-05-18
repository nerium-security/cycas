Supported artifacts
===================

All Velociraptor artifacts that can be used by Cycas to post-process raw
evidence are listed below.

.. note::
    Velociraptor artifacts are designed to run against a live Windows system.
    To run them against a collected ZIP containing raw evidence instead, Cycas
    generates a *remapping file* - a YAML configuration that instructs Velociraptor
    to treat the ZIP's contents as if they were a live machine, redirecting
    filesystem and registry paths into the archive.

    The remapping file is dynamically generated for each ZIP using the base
    configuration in ``velociraptor/artifacts/Custom.Generic.Utils.ZipRemap.yaml``.


Standard artifacts
------------------

.. list-table::
   :widths: 45 55
   :header-rows: 1

   * - Artifact
     - Description
   * - ``Windows.NTFS.MFT``
     - Master File Table - full filesystem metadata
   * - ``Windows.Forensics.Usn``
     - USN Journal ($UsnJrnl) - filesystem change history
   * - ``Windows.Sys.AppcompatShims``
     - Application compatibility shims
   * - ``Windows.Forensics.RecentApps``
     - Recently accessed files and applications from the registry
   * - ``Windows.Forensics.UserAccessLogs``
     - User Access Logs (UAL) - remote access and logon history
   * - ``Windows.Forensics.Shellbags``
     - Shellbags - folder browsing history
   * - ``Windows.Detection.Amcache``
     - Amcache.hve - file execution and installation history
   * - ``Windows.System.Powershell.PSReadline``
     - PowerShell command history
   * - ``Windows.System.TaskScheduler``
     - Scheduled tasks
   * - ``Windows.Forensics.RecycleBin``
     - Recycle Bin contents and metadata
   * - ``Windows.EventLogs.Evtx``
     - Windows Event Logs (all .evtx files)
   * - ``Windows.Registry.AppCompatCache``
     - AppCompatCache (Shimcache) - program execution evidence
   * - ``Windows.Forensics.Prefetch``
     - Prefetch files - execution evidence
   * - ``Windows.Sys.Programs``
     - Installed programs
   * - ``Windows.Forensics.JumpLists``
     - Jump Lists - recently/frequently accessed files per application
   * - ``Windows.Forensics.Timeline``
     - Windows Timeline / Activity history
   * - ``Windows.Forensics.SRUM``
     - System Resource Usage Monitor - process, network, and energy usage history


Custom artifacts
----------------

The following artifacts also work with Cycas but required minor customisation,
indicated by the ``Custom`` prefix.

.. list-table::
   :widths: 45 55
   :header-rows: 1

   * - Artifact
     - Description
   * - ``Custom.Windows.Forensics.Bam``
     - Background Activity Moderator - records of executed binaries
   * - ``Custom.Windows.Forensics.SAM``
     - SAM database - local account and group information
   * - ``Custom.Windows.Registry.Interfaces``
     - Network interface registry keys
   * - ``Custom.Windows.Registry.NTUser``
     - NTUser.dat registry hive contents
   * - ``Custom.Windows.Registry.RDP``
     - RDP-related registry keys (MRU, client connection history)
   * - ``Custom.Windows.Registry.RecentDocs``
     - Recently opened documents from the registry
   * - ``Custom.Windows.Registry.UserAssist``
     - Programs run by each user, with run counts, from UserAssist registry keys
   * - ``Custom.Windows.Sys.Users``
     - Local user accounts