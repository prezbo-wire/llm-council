"""Configuration for the LLM Council."""

import os
from dotenv import load_dotenv

load_dotenv()

# OpenRouter API key
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# Council members - list of OpenRouter model identifiers
COUNCIL_MODELS = [
    "openai/gpt-5.1",
    "google/gemini-3-pro-preview",
    "anthropic/claude-sonnet-4.5",
    "x-ai/grok-4",
]

# Chairman model - synthesizes final response
CHAIRMAN_MODEL = "openai/gpt-oss-20b"

# Display names for models (human-readable)
MODEL_DISPLAY_NAMES = {
    "openai/gpt-5.1": "GPT-5.1",
    "google/gemini-3-pro-preview": "Gemini 3 Pro",
    "anthropic/claude-sonnet-4.5": "Claude Sonnet 4.5",
    "x-ai/grok-4": "Grok 4",
    "openai/gpt-oss-20b": "Chairman (GPT-OSS)",
}

# OpenRouter API endpoint
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

# Data directory for conversation storage
DATA_DIR = "data/conversations"
