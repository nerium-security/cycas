Easy installation
=================

The easy installation uses the ``easy_install.py`` wizard to automatically provision
all required Azure infrastructure and configure your ``.env`` file. This is the
recommended approach for production deployments running at scale via Azure
Functions.

What gets created
-----------------

- Azure Data Explorer cluster and database
- Storage account (blob container, queue, table)
- Input source configuration (Blob, SAS, and/or SFTP)
- Two Azure Function Apps (watcher and processor)
- Application Insights for monitoring
- (Optional) Key Vault for ZIP password storage

Prerequisites
-------------

**Python 3.13**

.. code-block:: bash

   sudo add-apt-repository ppa:deadsnakes/ppa
   sudo apt update
   sudo apt install python3.13
   sudo apt install python3.13-venv

**Azure CLI** — used for authentication:

.. code-block:: bash

   curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash

**Azure permissions** — Contributor access on the target resource group (or
subscription if you want to create a new resource group).

**Azure Functions Core Tools** — installed automatically by the wizard if not
already present (requires ``npm``).

Steps
-----

1. Clone the repository and create a virtual environment:

   .. code-block:: bash

      git clone <repository-url>
      cd cycas
      python3.13 -m venv .venv
      source .venv/bin/activate

2. Install the Cycas package:

   .. code-block:: bash

      pip install .

3. Log in to Azure:

   .. code-block:: bash

      az login

4. Run the install wizard:

   .. code-block:: bash

      python easy_install.py

   The wizard guides you through seven steps and saves progress automatically.
   If it is interrupted during provisioning, re-run it and choose to resume
   the saved session. See :doc:`../guides/runningatscale` for a full walkthrough.

Resuming a failed install
-------------------------

If the script is interrupted during provisioning, simply re-run it:

.. code-block:: bash

   python easy_install.py

The wizard will detect the saved session and offer to resume from where it
left off. If you want to start fresh, choose **N** when prompted.
