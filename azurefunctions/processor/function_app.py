import azure.functions as func
from core.pipeline.runner import run_azurefunction_processor

app = func.FunctionApp()

@app.queue_trigger(arg_name="azqueue", queue_name="triagepackages",
                               connection="CYCAS_DATASTORAGE")
def queue_trigger(azqueue: func.QueueMessage):

    run_azurefunction_processor(mode='azurefunction', messagequeue = [azqueue])