'''
This script processes zip files stored in a sftp server.
'''

from sharedlib.pipeline.runner import run_localdevice

if __name__ == '__main__':
    run_localdevice(triagepackage_source='sftp')
