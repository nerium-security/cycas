Obfuscating the Velociraptor client
===================================

This guide explains how to obfuscate the Velociraptor client on **both Windows
and Linux**. The steps differ per platform, so follow the `Windows`_ or
`Linux`_ section that applies to your endpoints (or both).

.. contents:: On this page
   :local:
   :depth: 2


Purpose
-------

When you deploy a Velociraptor agent to a client's endpoints during an
incident, you do not want an attacker who is still active on the network to
recognise it as an incident-response tool. If they spot it, they may change
their behaviour, try to disable the agent, or destroy evidence.

To avoid that, you can rebrand ("obfuscate") the Velociraptor client so it no
longer looks like Velociraptor. The approach differs per platform:

- On **Windows** you rebrand the binary (icon, file properties, name) and
  package it into an MSI, which Cycas uses as a custom installer template.
- On **Linux** the Cycas installer rebuilds the deb/rpm packages under a name
  you choose - no manual binary editing is needed.

.. note::

   This rebrands the client's appearance and identity only - it does not alter
   Velociraptor's behaviour. Use it for authorised incident-response engagements
   where you are entitled to deploy monitoring to the endpoints.

Choose a **distinctive, inconspicuous name** The examples below use ``CustomAgent`` 
as a placeholder - substitute your own.


Downsides and caveats
---------------------

Rebranding is useful, but weigh the trade-offs before relying on it:

- **It is shallow by design.** It changes the client's identity and appearance,
  not its behaviour. A determined attacker can still fingerprint it by its
  process and service behaviour, network traffic, or on-disk strings. Treat
  obfuscation as raising the bar, not as a guarantee of concealment.
- **Coordinate with the client's defenders.** A renamed, unfamiliar agent can
  look like malware to the client's own security team. Tell them what you are
  deploying so it is not mistaken for a threat.
- **Windows: it breaks the code signature.** The rebuilt Windows binary is no
  longer signed by Velocidex. Unless you re-sign it with your own code-signing
  certificate, it ships **unsigned**, which Windows SmartScreen, antivirus and
  EDR may flag.
- **Windows: it adds maintenance.** You must repeat the Windows rebuild for
  every Velociraptor release you want to deploy. (On Linux the installer
  re-creates the renamed packages automatically on each run.)


Windows
-------

On Windows you rebrand the release binary, then package it into an MSI that
Cycas repacks with the per-deployment client config.

Overview:

#. Download the Velociraptor source and a release binary.
#. Rebrand the binary (icon + version info) with Resource Hacker.
#. Build an MSI around the rebranded binary with the WiX Toolset.
#. Point the Cycas installer at that MSI as a custom template.

Prerequisites
~~~~~~~~~~~~~~

- A Windows build host with the `WiX Toolset
  <https://wixtoolset.org/>`_ installed (provides ``candle.exe`` and
  ``light.exe``).
- `Resource Hacker <https://www.angusj.com/resourcehacker/>`_.
- A 64x64 ``.ico`` file with the icon you want the rebranded client to use.

Download the source and release binary
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Clone the Velociraptor project and download the release binary you want to
rebrand:

.. code-block:: bash

   git clone https://github.com/Velocidex/velociraptor.git

Download the matching release executable from the `Velociraptor releases page
<https://github.com/Velocidex/velociraptor/releases>`_ and place it in the
project's output folder, for example:

.. code-block:: text

   ./velociraptor-vX.XX/docs/output/

Rebrand the binary with Resource Hacker
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Resource Hacker lets you edit the properties embedded in a Windows executable.
You change two things: the **icon** and the **version info**.

#. Start Resource Hacker and open the release binary you placed in the output
   folder.

   .. image:: /_static/images/obfuscation_resourcehacker_open.png

#. **Replace the icon.** Under the *Icon Group* folder, right-click
   ``APP : 0`` and choose **Replace Icon**, then select your 64x64 ``.ico``
   file to replace the Velociraptor icon.

   .. image:: /_static/images/obfuscation_resourcehacker_icon.png

#. **Edit the version info.** In the ``StringFileInfo`` block, change the
   property values (company name, product name, and so on) to the rebranded
   values you want to use.

   .. image:: /_static/images/obfuscation_resourcehacker_versioninfo.png

#. **Edit the description** to the name you want shown for the file.

   .. image:: /_static/images/obfuscation_resourcehacker_description.png

#. Click the **compile/save** button (top-left) to write all changes back to
   the binary.

Rename the rebranded binary to the name you chose for this engagement (for
example ``CustomAgent.exe``).

Build the MSI with the WiX Toolset
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Package the rebranded binary into an MSI using a WiX source file. On the build
host, prepare an input folder containing:

- the WiX source for the installer (e.g. ``CustomAgent_amd64.xml`` /
  ``CustomAgent_x86.xml``), with the product name and version set to your
  rebranded values,
- the rebranded binary (e.g. ``CustomAgent.exe``),
- a ``client.config.yaml`` placeholder (the Cycas installer re-injects the real
  client config later - see below).

.. important::

   The installer re-injects a client config whose Windows writeback file is
   placed in ``$ProgramFiles\<prefix>\<prefix>.writeback.yaml``, where
   ``<prefix>`` is the *Obfuscated client name* you enter during
   ``easy_install.py``. Set that name to the **install-folder** ``Name`` of the
   ``<Directory>`` element in your WiX source (the folder created under
   ``Program Files``), so the writeback file lands in the install directory. If
   they differ the client still runs, but its writeback is written to a separate
   ``<prefix>`` folder instead of next to the binary.

Compile the MSI with ``candle`` and ``light``:

.. code-block:: bat

   REM x64
   cd <input-folder>
   ..\bin\candle.exe CustomAgent_amd64.xml -arch x64 -ext "<wix>\bin\WixUtilExtension.dll"
   ..\bin\light.exe  CustomAgent_amd64.wixobj  -ext "<wix>\bin\WixUtilExtension.dll"

   REM x86
   cd <input-folder>
   ..\bin\candle.exe CustomAgent_x86.xml -arch x86 -ext "<wix>\bin\WixUtilExtension.dll"
   ..\bin\light.exe  CustomAgent_x86.wixobj  -ext "<wix>\bin\WixUtilExtension.dll"

The output should look similar to this:

.. image:: /_static/images/obfuscation_wix_build_output.png

You now have an obfuscated MSI for each architecture. These are the **MSI
templates** you give to Cycas.

Configure it in the easy installer
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Cycas does not ship the obfuscated binary - you supply your own MSI template,
and the installer repacks the per-deployment client config into it (with
``velociraptor config repack --msi``) to produce the final installer. The
output is written to ``Velociraptor-ClientInstallers/obfuscated/`` alongside the
standard installers.

#. **Host the MSI templates** somewhere the installer can download them, such as
   a blob with a read SAS URL.

#. **Point the installer at them.** During ``easy_install.py``, at the MSI
   template prompts, paste the SAS URL for each architecture::

      x64 MSI template [standard installer] - Enter to keep, or paste a custom MSI URL: <x64 SAS URL>
      x86 MSI template [standard installer] - Enter to keep, or paste a custom MSI URL: <x86 SAS URL>

   Equivalently, set them in ``.env`` before an unattended run:

   .. code-block:: ini

      VELOCIRAPTOR_SERVER_MSI_AMD64_URL=https://<account>.blob.core.windows.net/<container>/CustomAgent_amd64.msi?<sas>
      VELOCIRAPTOR_SERVER_MSI_X86_URL=https://<account>.blob.core.windows.net/<container>/CustomAgent_x86.msi?<sas>

#. **Name the output.** The obfuscated installers are named with the prefix from
   ``VELOCIRAPTOR_SERVER_OBFUSCATED_MSI_PREFIX``. Set it to the name you chose
   for this engagement so you get, for example,
   ``CustomAgent_client_windows_install_x64.msi``:

   .. code-block:: ini

      VELOCIRAPTOR_SERVER_OBFUSCATED_MSI_PREFIX=CustomAgent

The installer validates that each URL points to a real MSI before accepting it.
When no custom template is given, only the standard Velociraptor installers are
built.


Linux
-----

On Linux there is no icon or version-info to edit. Instead, the Cycas installer
rebuilds the deb/rpm client packages under a name you choose, renaming the
installed binary, the systemd/SysV service, the install paths, the service
user/group and the package filename - all to your name.

Configure it in the easy installer
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Set a custom Linux client name during ``easy_install.py`` at the Linux client
name prompt::

   Linux client name [velociraptor_client] - Enter to keep, or type a custom name: customagent

Equivalently, set it in ``.env`` before an unattended run:

.. code-block:: ini

   VELOCIRAPTOR_SERVER_LINUX_CLIENT_NAME=customagent

The name must be a valid deb/rpm package and systemd service name: lowercase
letters, digits and hyphens, starting with a letter.

The installer then builds an extra set of renamed deb/rpm packages into
``Velociraptor-ClientInstallers/obfuscated/``, next to the standard
``velociraptor_client`` packages. When no name is given, only the standard
packages are built.
