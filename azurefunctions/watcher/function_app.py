import azure.functions as func
from core.pipeline.runner import run_azurefunction_watcher

app = func.FunctionApp()

@app.timer_trigger(schedule="*/1 * * * *", arg_name="mytimer")
def run_every_minute(mytimer: func.TimerRequest) -> None:
    
    run_azurefunction_watcher()