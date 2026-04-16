Standalone
==========

The Python script :file:`scripts/standalone/process_with_velo.py` post-processes ZIP files
containing raw artifact files collected with Velociraptor. For each ZIP file
it extracts the contents, runs Velociraptor to parse the artifacts, and writes
the results to CSV, JSON, or JSONL output files alongside a summary report.

Note that the script does not upload results to Azure Data Explorer.


.. contents:: Table of Contents
   :depth: 2
   :local:
   :backlinks: none
 

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


Building a standalone executable
--------------------------------

To distribute the post-processing script as a standalone executable (no Python required):

.. code-block:: bash

    pip install pyinstaller
    pyinstaller --onefile --collect-all core scripts/standalone/process_with_velo.py

The executable will be available in the `dist/` folder.
