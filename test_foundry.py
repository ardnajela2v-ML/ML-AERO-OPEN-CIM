from openai import OpenAI
from azure.identity import DefaultAzureCredential, get_bearer_token_provider

endpoint = "https://esajoraj-resource.services.ai.azure.com/openai/v1"
deployment_name = "gpt-6-sol"

token_provider = get_bearer_token_provider(
    DefaultAzureCredential(),
    "https://ai.azure.com/.default",
)

client = OpenAI(
    base_url=endpoint,
    api_key=token_provider,
)

response = client.responses.create(
    model=deployment_name,
    input="Responde únicamente: conexión de prueba exitosa.",
)

print(response.output_text)
