Post-processing raw artifacts
=============================

The script :file:`scripts/standalone/process_with_velo.py` post-processes ZIP files
containing raw artifacts collected with Velociraptor. For each ZIP it extracts
the contents, runs Velociraptor to parse the artifacts, and writes the results to
CSV, JSON, or JSONL output files alongside a summary report. Optionally it can ingest
data into Azure Data Explorer as well.

**Use this script when** you want to post-process a small number of
triage packages locally, and inspect the results on your own system. It requires no 
Azure connection and produces output files you can examine directly.

**Do not use this script** for production or large-scale collection, as there is no
tracking of which triage packages were already uploaded. Meaning that you might 
upload duplicate data.

Installation
------------

.. code-block:: bash
 
   # Clone the repository
   git clone https://github.com/nerium-security/cycas.git && cd cycas

   # Create an virtual environment
   python3 -m venv .venv

   # Activate the virtual environment
   source .venv/bin/activate

   # Install required packages
   pip3 install .


Usage
-----

Browse to the script directory 

.. code-block:: bash

   cd scripts/standalone/

Process a single ZIP file:
 
.. code-block:: bash

   python3 process_with_velo.py -i collection.zip
 
Process multiple ZIP files using a wildcard:
 
.. code-block:: bash
 
   python3 scripts/standalone/process_with_velo.py -i *.zip
 
Process ZIP files and write output to a specific folder:
 
.. code-block:: bash
 
   python3 scripts/standalone/process_with_velo.py -i *.zip -f /output/results

Process a ZIP file and ingest the results into Azure Data Explorer:

.. code-block:: bash
 
   python3 scripts/standalone/process_with_velo.py -i *.zip --ingest



Building a standalone executable
--------------------------------

To distribute the post-processing script as a standalone executable (no Python required):

.. code-block:: bash

    pip install pyinstaller
    pyinstaller --onefile --collect-all core scripts/standalone/process_with_velo.py

The executable will be available in the `dist/` folder.
