'''
Azure Function script that processes zip files.
'''

from core.pipeline.runner import run_azurefunction_processor

if __name__ == '__main__':
    run_azurefunction_processor(mode='manual')
    