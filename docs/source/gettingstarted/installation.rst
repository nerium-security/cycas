Installation
============

Cycas can be installed in two ways:

- :ref:`Easy installation (recommended) <easy-installation>` - uses the
  ``easy_install.py`` wizard to automatically provision all required Azure
  infrastructure and configure your ``.env`` file.
- :ref:`Manual installation <manual-installation>` - you provision the Azure
  resources yourself and configure Cycas by filling in the ``.env`` file by
  hand. Use this if you prefer to manage infrastructure through your own tooling 
  (Terraform, Bicep, Azure, Portal, etc.).

.. contents:: Contents
   :local:
   :depth: 2


.. _installation-prerequisites:

Prerequisites
-------------

.. code-block:: bash

   # Python 3.13
   sudo add-apt-repository ppa:deadsnakes/ppa
   sudo apt update
   sudo apt install python3.13
   sudo apt install python3.13-venv

   # Azure CLI - used for authentication
   sudo apt install curl
   curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash

   # Azure Functions Core Tools - used to deploy the watcher/processor functions
   curl -sLS https://packages.microsoft.com/keys/microsoft.asc | sudo gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg
   echo "deb [arch=amd64 signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/repos/microsoft-ubuntu-$(lsb_release -cs)-prod $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/microsoft-prod.list
   sudo apt update
   sudo apt install azure-functions-core-tools-4

**Azure permissions** - Owner access on the target resource group or contributor combined with the User Access Administrator role (or
owner on the subscription if you want to create a new resource group).

Clone the repository, create a virtual environment, and install Cycas:

.. code-block:: bash

   sudo apt install git
   git clone https://github.com/nerium-security/cycas
   cd cycas
   python3.13 -m venv .venv
   source .venv/bin/activate
   pip install .

Log in to Azure:

.. code-block:: bash

   az login

.. note::

   If no browser opens (WSL, SSH, headless) and device code login is blocked
   by Conditional Access (e.g. passkey or trusted device policies), print the
   sign-in URL instead:

   .. code-block:: bash

      BROWSER=echo az login

   Open the URL in a browser on the same machine (e.g. Edge on the Windows
   host for WSL). It redirects to ``localhost``, so other devices won't work.


.. _easy-installation:

Easy installation (recommended)
--------------------------------

The easy installation uses the ``easy_install.py`` wizard to automatically provision
all required Azure infrastructure and configure your ``.env`` file. This is the
recommended approach for setting up Cycas.

What gets created
~~~~~~~~~~~~~~~~~~

- Azure Data Explorer cluster and database
- Storage account (blob container, queue, table)
- Input source configuration (Blob, SAS, and/or SFTP)
- Two Azure Function Apps (watcher and processor)
- Application Insights for monitoring
- Key Vault for storing offline ZIP collection password
- Web app VM running the status dashboard
- Velociraptor server VM with a static IP, client installers and hunts
- Offline Velociraptor collectors for Windows, Linux and macOS (uploaded to the input container)

Steps
~~~~~

1. Clone the repository, create a virtual environment, and install the Cycas
   package, then log in to Azure - see :ref:`Prerequisites <installation-prerequisites>`
   above.

2. Run the install wizard:

   .. code-block:: bash

      python easy_install.py

   The wizard guides you through nine steps in Azure Functions mode (five in
   local mode) and saves progress automatically. If it is interrupted during
   provisioning, re-run it and choose to resume the saved session.

Resuming a failed install
~~~~~~~~~~~~~~~~~~~~~~~~~~

If the script is interrupted during provisioning, simply re-run it:

.. code-block:: bash

   python easy_install.py

The wizard will detect the saved session and offer to resume from where it
left off. If you want to start fresh, choose **N** when prompted.


.. _manual-installation:

Manual installation
--------------------

The manual installation gives you full control over Azure resource creation:
you provision the resources yourself and configure Cycas by filling in the
``.env`` file. Use this if you prefer to deploy all Azure resources through 
your own tooling.

.. warning::

   Unattended mode is supported but has not yet been tested in production.

Configure env variables
~~~~~~~~~~~~~~~~~~~~~~~~

First copy the ``.env_example`` to ``.env``:

.. code-block:: bash

   cp .env_example .env

Open ``.env`` in a text editor and fill in the values for your environment.

.. note::

   Boolean values must be set to ``true`` or ``false`` (lowercase).
   Leave a variable empty (``VARIABLE=``) rather than removing it - all
   keys must be present for the configuration to load correctly.

Running in unattended mode
~~~~~~~~~~~~~~~~~~~~~~~~~~

You can provision everything automatically by running the
``easy_install.py`` wizard in **unattended mode**, driven by a pre-configured
``.env`` file:

.. code-block:: bash

   python easy_install.py --unattended

The ``--unattended`` flag is equivalent to setting ``CYCAS_UNATTENDED=1`` in the
environment; use whichever is more convenient (for example, exporting the
variable in a CI job). You do not need both.