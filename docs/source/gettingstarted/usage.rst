Usage
=====

Once Cycas is installed and configured (see :doc:`installation`), usage
comes down to two things: getting a triage package into the pipeline,
and tracking it as it moves through processing.

.. contents:: On this page
   :local:
   :depth: 2


Upload triage package
----------------------

Cycas is intended to run on Azure Functions to benefit from horizontal
scaling, which allows for processing multiple ZIPs simultaneously.

Once you've completed the installation, upload a ZIP file containing 
the endpoint data collected with Velociraptor. If the configured input source 
is ``blob``, upload the ZIP file to the ``uploads`` container as shown below.

.. image:: /_static/images/runningatscale_uploadblob.png

The watcher function picks up the new ZIP and queues it for processing.


**Optional: Running locally**

Cycas can also run as a standalone script, for example on a (powerful)
forensic workstation, without deploying Azure Functions. There is no upload
step. The pipeline pulls ZIPs directly from whichever input source is
enabled in ``.env`` (``localfolder``, ``blob``, ``sas``, or ``sftp``).

.. code-block:: bash

   python scripts/pipeline/zip2adx.py

The script processes every matching ZIP it finds at the configured source
and exits when done. Run it again to pick up any new files.


Track progress
--------------

Cycas ships a web dashboard that gives a live, readable view of the 
processing status, without needing the Azure Portal. The pipeline 
works without the dashboard so it's optional to run it, but it makes
it easy to track status.

When the setup was launched with ``easy_install.py``, it shows you how to 
run the dashboard: 

.. image:: /_static/images/setup-finalised.png

.. code-block:: bash

   python3 gunicorn --bind=0.0.0.0:4040 --timeout 600 webapp.app:app

The dashboard is available at ``http://0.0.0.0:4040``.

.. image:: /_static/images/webapp_status.png


It shows each ZIP's filename, extracted hostname, status, source, size, and
elapsed duration, and lets you filter or search entries and retry failed or
stuck runs directly from the page. 

**Optional: Tracking status without dasbhoard**

The dasbhoard simply reads the Azure Storage Account table 
which contains the actual status of each ZIP, which is tracked
as an entry in the table ``statusupdate`` You can inspect progress 
directly using the Azure Portal's storage browser:

.. image:: /_static/images/runningatscale_statustracking.png

To force a ZIP to be reprocessed, delete its entry from the table - it will
be picked up again as if it were new.

.. image:: /_static/images/runningatscale_deleteentry.png
