from google import genai
from google.genai import types
from dotenv import load_dotenv
from src.log import logger
load_dotenv(override=True)

def summarize(input_prompt, **args):
    PROJECT_ID = args.get("PROJECT_ID")
    MODEL_NAME = args.get("MODEL_NAME")
    GOOGLE_CLOUD_LOCATION = args.get("GOOGLE_CLOUD_LOCATION")
    logger.info(f"Gemini start processing summary")
    try:
        client = genai.Client(
            vertexai=True,
            project=PROJECT_ID,
            location=GOOGLE_CLOUD_LOCATION,
        )
        
        generate_content_config = types.GenerateContentConfig(
            temperature = 0,
            top_p = 1,
            seed = 0,
            max_output_tokens = 25000,
            safety_settings = [types.SafetySetting(
                category="HARM_CATEGORY_HATE_SPEECH",
                threshold="OFF"
            ),types.SafetySetting(
                category="HARM_CATEGORY_DANGEROUS_CONTENT",
                threshold="OFF"
            ),types.SafetySetting(
                category="HARM_CATEGORY_SEXUALLY_EXPLICIT",
                threshold="OFF"
            ),types.SafetySetting(
                category="HARM_CATEGORY_HARASSMENT",
                threshold="OFF"
            )],
            thinking_config=types.ThinkingConfig(
                include_thoughts=True,
                thinking_budget=512,
            ),
        )
        # Construct the content for the API request
        contents = [
            types.Content(
                role="user",
                parts=[
                    types.Part.from_text(text=input_prompt),
                    types.Part.from_text(text="Summarize please")
                ]
            )
        ]
        response = client.models.generate_content(model=MODEL_NAME, contents=contents, config = generate_content_config)
        logger.info(f"Gemini finished processing summary")
        return response.text
    except Exception as e:
        logger.error(f"Gemini error processing summary", extra={'json_payload': str(e)})