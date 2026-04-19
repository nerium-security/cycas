'''
Azure Function script that watches for new zip files
'''

from core.pipeline.runner import run_azurefunction_watcher

if __name__ == '__main__':
    run_azurefunction_watcher()
