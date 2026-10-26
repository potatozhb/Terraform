
import os

from dotenv import load_dotenv


load_dotenv()
endpoint = os.environ["AZURE_AI_PROJECT_ENDPOINT"]
model_deployment_name = os.getenv("MODEL_DEPLOYMENT_NAME")

print(f"Endpoint: {endpoint}")
print(f"Model Deployment Name: {model_deployment_name}")