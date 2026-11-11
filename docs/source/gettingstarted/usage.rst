First Steps
===========

Once Cycas is installed (see :doc:`installation`), ``easy_install.py`` has
provisioned a working environment for you: a Velociraptor server, the Cycas
ingestion pipeline, and the status dashboard. This page walks through your
first steps - logging in to the Velociraptor server and the dashboard, and
confirming that ingestion is wired up.

Once you are set up, see the guides for
:doc:`starting a mass collection </guides/masscollection>` and
:doc:`analysing the results in ADX </guides/analyse-results-adx>`.

.. contents:: On this page
   :local:
   :depth: 2


Log in to the Velociraptor server
----------------------------------

The installer deploys a Velociraptor server on a VM with a static IP. Its URL
is shown in the summary printed at the end of ``easy_install.py``:

.. image:: /_static/images/setup-finalised.png

Open ``https://<your-server>/`` in a browser. Access to the GUI is restricted
to the IP ranges you allowed during installation.

**Credentials**

- **Username:** ``admin`` (plus any extra users you configured).
- **Password:** generated during installation and stored as the Azure Key Vault
  secret ``velociraptor-server-admin-password``.

Retrieve it from the Azure Portal: open the Key Vault created by the installer
(its name is in the install summary), go to **Secrets**, and read
``velociraptor-server-admin-password``.

.. image:: /_static/images/keyvault-secret.png

**Prepared hunts**

The server comes preconfigured with a set of hunts (``HUNT001``-``HUNT008``) that
collect live data and forensic triage, and run Sigma detection on the endpoints'
event logs with the `Hayabusa rules <https://sigma.velocidex.com/docs/sigma_in_velociraptor/>`_
(``HUNT008``). They are **created paused** - start them from the GUI once you
start deploying the Velociraptor clients.

.. image:: /_static/images/velociraptor_hunts.png

Ingestion into ADX is already configured
-----------------------------------------

You do not need to wire up ingestion yourself. The Velociraptor server deployed
by ``easy_install.py`` is set up to push results into Azure Data Explorer
through Cycas automatically:

- **Hunt results** - ``Server.Utils.BackupAzure`` ships each finished hunt flow
  to Blob Storage, where Cycas picks it up and ingests it into ADX.
- **Triage packages** - the ``Custom.Windows.Triage.Targets.AzureUpload``
  artifact is imported with the Cycas input container's SAS URL and ZIP password
  already filled in, so collections upload straight to Cycas.

In both cases the data lands in the same ADX cluster, queryable together with
KQL.

Log in to the dashboard
-----------------------

Cycas ships a web dashboard that gives a live, readable view of the ingestion
status without needing the Azure Portal. If you deployed it on a VM, its URL
(HTTPS with Let's Encrypt, limited to your allowed IPs) is in the install
summary; open ``https://<your-dashboard>/``.

**Credentials**

- **Username:** ``cycas``.
- **Password:** the Azure Key Vault secret ``cycas-webapp-password``, retrieved
  the same way as the Velociraptor password above.

.. image:: /_static/images/webapp_status.png

The dashboard shows each ZIP's filename, extracted hostname, status, source,
size and elapsed duration, and lets you filter, search, and retry failed or
stuck runs directly from the page.

.. note::

   If you did not deploy the dashboard VM, you can run the dashboard locally
   instead:

   .. code-block:: bash

      gunicorn --bind=0.0.0.0:4040 --timeout 600 webapp.app:app

   It is then available at ``http://localhost:4040``.