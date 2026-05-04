Running at scale
================

Running Cycas at scale uses Azure Functions to automate the full pipeline:
a **watcher** function checks for new triage packages every minute and puts
them on a queue, and a **processor** function picks them up and runs the
ingestion pipeline. The ``easy_install.py`` wizard provisions all required Azure
infrastructure and writes the connection strings to your ``.env`` file.

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

**Azure Functions Core Tools** — installed automatically by ``easy_install.py`` if
not already present (requires ``npm``).

**Azure permissions** — you need Contributor access on the target resource
group (or subscription if you want to create a new resource group).

Installation
------------

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

Wizard walkthrough
------------------

The wizard guides you through seven steps. Progress is saved automatically
after input collection — if the script is interrupted during provisioning,
re-run it and choose to resume the saved session.

**Step 1 — Authentication**
   Authenticates using your active ``az login`` session and lists available
   subscriptions for you to choose from.

**Step 2 — Resource Group**
   Select an existing resource group or create a new one. If creating new,
   you will also select an Azure region.

**Step 3 — Azure Data Explorer**
   Configure the ADX cluster name, database name, and SKU. Two SKUs are
   available:

   - *Dev (No SLA)* — development and testing, ~$2.40/day
   - *Standard_D11_v2* — production, ~$5.50/day per node

   You can optionally search for Azure AD users to grant them cluster admin
   access.

**Step 4 — Storage Account**
   Configure the storage account used for the queue, status table, and blob
   container that receives triage packages.

**Step 5 — Input Sources**
   Select which source(s) Cycas should watch for new triage packages. Multiple
   sources can be enabled simultaneously:

   - **Blob** *(recommended)* — Azure Blob Storage via managed identity, using
     the storage account provisioned in the previous step.
   - **SAS** — Azure Blob Storage via a Shared Access Signature token. Paste
     the full SAS URL when prompted.
   - **SFTP** — Remote file server via SSH. The private key is read from Key
     Vault at runtime.

**Step 6 — Azure Functions**
   Name the watcher and processor function apps and their dedicated storage
   accounts. An Application Insights instance is also created here for
   monitoring.

**Step 7 — Key Vault (optional)**
   If triage packages are ZIP-password-protected, a Key Vault is created and
   the password is uploaded as a secret. Skip this step if packages are not
   password-protected.

After input collection the wizard displays a full summary and asks for
confirmation before provisioning anything.

After installation
------------------

Once the wizard completes:

- Your ``.env`` file contains all connection strings and is ready for use.
- Both function apps are deployed and running.
- The watcher triggers every minute and puts new triage packages on the queue.
- The processor picks them up and runs the ingestion pipeline.

Resuming a failed install
-------------------------

If the script is interrupted during provisioning, simply re-run it:

.. code-block:: bash

   python easy_install.py

The wizard will detect the saved session and offer to resume from where it
left off. If you want to start fresh, choose **N** when prompted.
