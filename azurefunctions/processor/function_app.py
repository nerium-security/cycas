import azure.functions as func
from core.pipeline.runner import run_azurefunction_processor

app = func.FunctionApp()

@app.queue_trigger(arg_name="azqueue", queue_name="triagepackages",
                               connection="AzureWebJobsStorage") 
def queue_trigger(azqueue: func.QueueMessage):
    message = azqueue.get_body().decode('utf-8')
    run_azurefunction_processor(mode='azurefunction', messagequeue = [message])