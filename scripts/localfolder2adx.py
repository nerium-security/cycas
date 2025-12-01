'''
This script processes zip files stored in a local folder 
on the system you are running the script from.
'''

from sharedlib.pipeline.runner import run_localdevice

if __name__ == '__main__':
    run_localdevice(triagepackage_source='localfolder')
    