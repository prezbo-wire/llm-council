"""OpenRouter API client for making LLM requests."""

import httpx
import json
import time
from typing import List, Dict, Any, Optional, Callable, AsyncIterator
from .config import OPENROUTER_API_KEY, OPENROUTER_API_URL


async def query_model_stream(
    model: str,
    messages: List[Dict[str, str]],
    on_delta: Callable[[str], None],
    timeout: float = 120.0
) -> Optional[Dict[str, Any]]:
    """
    Stream response from a single model, calling on_delta for each chunk.

    Args:
        model: OpenRouter model identifier
        messages: List of message dicts with 'role' and 'content'
        on_delta: Callback function called with each text chunk
        timeout: Request timeout in seconds

    Returns:
        Response dict with 'content' (full text) and 'latency_ms', or None if failed
    """
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
    }

    start_time = time.time()
    full_content = ""

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST",
                OPENROUTER_API_URL,
                headers=headers,
                json=payload
            ) as response:
                response.raise_for_status()

                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str.strip() == "[DONE]":
                            break
                        try:
                            data = json.loads(data_str)
                            if "choices" in data and len(data["choices"]) > 0:
                                delta = data["choices"][0].get("delta", {})
                                content = delta.get("content", "")
                                if content:
                                    full_content += content
                                    await on_delta(content)
                        except json.JSONDecodeError:
                            continue

        latency_ms = int((time.time() - start_time) * 1000)
        return {
            "content": full_content,
            "latency_ms": latency_ms
        }

    except Exception as e:
        print(f"Error streaming from model {model}: {e}")
        return None


async def query_model(
    model: str,
    messages: List[Dict[str, str]],
    timeout: float = 120.0
) -> Optional[Dict[str, Any]]:
    """
    Query a single model via OpenRouter API.

    Args:
        model: OpenRouter model identifier (e.g., "openai/gpt-4o")
        messages: List of message dicts with 'role' and 'content'
        timeout: Request timeout in seconds

    Returns:
        Response dict with 'content' and optional 'reasoning_details', or None if failed
    """
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": model,
        "messages": messages,
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                OPENROUTER_API_URL,
                headers=headers,
                json=payload
            )
            response.raise_for_status()

            data = response.json()
            message = data['choices'][0]['message']

            return {
                'content': message.get('content'),
                'reasoning_details': message.get('reasoning_details')
            }

    except Exception as e:
        print(f"Error querying model {model}: {e}")
        return None


async def query_models_parallel(
    models: List[str],
    messages: List[Dict[str, str]]
) -> Dict[str, Optional[Dict[str, Any]]]:
    """
    Query multiple models in parallel.

    Args:
        models: List of OpenRouter model identifiers
        messages: List of message dicts to send to each model

    Returns:
        Dict mapping model identifier to response dict (or None if failed)
    """
    import asyncio

    # Create tasks for all models
    tasks = [query_model(model, messages) for model in models]

    # Wait for all to complete
    responses = await asyncio.gather(*tasks)

    # Map models to their responses
    return {model: response for model, response in zip(models, responses)}
