Usage
=====
 
Cycas can run as intented on Azure Functions to benefit from 
horizontal scaling (processing multiple zip's simultaneously)
or locally on a (powerfull) forensic server.


Azure functions
---------------

Once you've completed the wizard (or configured the Azure resources manually), 
you can upload a ZIP file containing the endpoint data collected with Velociraptor. 
If the selected input-source is 'blob', you can upload the ZIP file to the 'uploads' 
container as depicted in the screenshot below.

.. image:: /_static/images/runningatscale_uploadblob.png

You can track the status using the storage browser:

.. image:: /_static/images/runningatscale_statustracking.png

If you manually want to re-run a zip file, delete the entry

.. image:: /_static/images/runningatscale_deleteentry.png


Local
-----

provides a single pipeline script, ``zip2adx.py``, that ingests
forensic triage packages into Azure Data Explorer (ADX). The input source
is controlled entirely through the ``.env`` file — no command-line arguments
are needed.

.. code-block:: bash

  python scripts/pipeline/zip2adx.py
 
.. contents:: On this page
   :local:
   :depth: 2
