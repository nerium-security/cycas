'''
Azure Function script that processes zip files.
'''

from sharedlib.pipeline.runner import run_azurefunction_processor

if __name__ == '__main__':
    run_azurefunction_processor(triagepackage_source='sas', mode='manual')
    