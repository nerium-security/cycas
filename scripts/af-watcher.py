from sharedlib.pipeline.runner import run_azurefunction_watcher

if __name__ == '__main__':
    run_azurefunction_watcher(triagepackage_source='blob')