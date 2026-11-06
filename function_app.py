import azure.functions as func

from foundry_decision_eval.main import app as evaluator

app = func.AsgiFunctionApp(app=evaluator, http_auth_level=func.AuthLevel.ANONYMOUS)
