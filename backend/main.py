"""FastAPI backend for LLM Council."""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Dict, Any
import uuid
import json
import asyncio

from . import storage
from .council import (
    run_full_council, generate_conversation_title,
    stage1_collect_responses, stage2_collect_rankings, stage3_synthesize_final,
    stage1_collect_responses_stream, stage2_collect_rankings_stream, stage3_synthesize_final_stream,
    calculate_aggregate_rankings
)
from .config import MODEL_DISPLAY_NAMES

app = FastAPI(title="LLM Council API")

# Enable CORS for local/LAN development
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_origin_regex=r"https?://.*:(5173|3000)",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class CreateConversationRequest(BaseModel):
    """Request to create a new conversation."""
    pass


class SendMessageRequest(BaseModel):
    """Request to send a message in a conversation."""
    content: str


class ConversationMetadata(BaseModel):
    """Conversation metadata for list view."""
    id: str
    created_at: str
    title: str
    message_count: int


class Conversation(BaseModel):
    """Full conversation with all messages."""
    id: str
    created_at: str
    title: str
    messages: List[Dict[str, Any]]


@app.get("/")
async def root():
    """Health check endpoint."""
    return {"status": "ok", "service": "LLM Council API"}


@app.get("/api/conversations", response_model=List[ConversationMetadata])
async def list_conversations():
    """List all conversations (metadata only)."""
    return storage.list_conversations()


@app.post("/api/conversations", response_model=Conversation)
async def create_conversation(request: CreateConversationRequest):
    """Create a new conversation."""
    conversation_id = str(uuid.uuid4())
    conversation = storage.create_conversation(conversation_id)
    return conversation


@app.get("/api/conversations/{conversation_id}", response_model=Conversation)
async def get_conversation(conversation_id: str):
    """Get a specific conversation with all its messages."""
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


@app.post("/api/conversations/{conversation_id}/message")
async def send_message(conversation_id: str, request: SendMessageRequest):
    """
    Send a message and run the 3-stage council process.
    Returns the complete response with all stages.
    """
    # Check if conversation exists
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Check if this is the first message
    is_first_message = len(conversation["messages"]) == 0

    # Add user message
    storage.add_user_message(conversation_id, request.content)

    # If this is the first message, generate a title
    if is_first_message:
        title = await generate_conversation_title(request.content)
        storage.update_conversation_title(conversation_id, title)

    # Run the 3-stage council process
    stage1_results, stage2_results, stage3_result, metadata = await run_full_council(
        request.content
    )

    # Add assistant message with all stages
    storage.add_assistant_message(
        conversation_id,
        stage1_results,
        stage2_results,
        stage3_result
    )

    # Return the complete response with metadata
    return {
        "stage1": stage1_results,
        "stage2": stage2_results,
        "stage3": stage3_result,
        "metadata": metadata
    }


@app.post("/api/conversations/{conversation_id}/message/stream")
async def send_message_stream(conversation_id: str, request: SendMessageRequest):
    """
    Send a message and stream the 3-stage council process.
    Returns Server-Sent Events as each stage completes.
    """
    # Check if conversation exists
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Check if this is the first message
    is_first_message = len(conversation["messages"]) == 0

    async def event_generator():
        try:
            # Add user message
            storage.add_user_message(conversation_id, request.content)

            # Start title generation in parallel (don't await yet)
            title_task = None
            if is_first_message:
                title_task = asyncio.create_task(generate_conversation_title(request.content))

            # Stage 1: Collect responses
            yield f"data: {json.dumps({'type': 'stage1_start'})}\n\n"
            stage1_results = await stage1_collect_responses(request.content)
            yield f"data: {json.dumps({'type': 'stage1_complete', 'data': stage1_results})}\n\n"

            # Stage 2: Collect rankings
            yield f"data: {json.dumps({'type': 'stage2_start'})}\n\n"
            stage2_results, label_to_model = await stage2_collect_rankings(request.content, stage1_results)
            aggregate_rankings = calculate_aggregate_rankings(stage2_results, label_to_model)
            yield f"data: {json.dumps({'type': 'stage2_complete', 'data': stage2_results, 'metadata': {'label_to_model': label_to_model, 'aggregate_rankings': aggregate_rankings}})}\n\n"

            # Stage 3: Synthesize final answer
            yield f"data: {json.dumps({'type': 'stage3_start'})}\n\n"
            stage3_result = await stage3_synthesize_final(request.content, stage1_results, stage2_results)
            yield f"data: {json.dumps({'type': 'stage3_complete', 'data': stage3_result})}\n\n"

            # Wait for title generation if it was started
            if title_task:
                title = await title_task
                storage.update_conversation_title(conversation_id, title)
                yield f"data: {json.dumps({'type': 'title_complete', 'data': {'title': title}})}\n\n"

            # Save complete assistant message
            storage.add_assistant_message(
                conversation_id,
                stage1_results,
                stage2_results,
                stage3_result
            )

            # Send completion event
            yield f"data: {json.dumps({'type': 'complete'})}\n\n"

        except Exception as e:
            # Send error event
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


@app.post("/api/conversations/{conversation_id}/message/stream/v2")
async def send_message_stream_v2(conversation_id: str, request: SendMessageRequest):
    """
    Send a message with per-model streaming updates (new UI format).
    Returns Server-Sent Events with granular member progress.
    """
    # Check if conversation exists
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Check if this is the first message
    is_first_message = len(conversation["messages"]) == 0

    async def event_generator():
        try:
            # Add user message
            storage.add_user_message(conversation_id, request.content)

            # Start title generation in parallel
            title_task = None
            if is_first_message:
                title_task = asyncio.create_task(generate_conversation_title(request.content))

            # Stage 1: Stream individual responses
            async def on_model_queued(model_id: str):
                display_name = MODEL_DISPLAY_NAMES.get(model_id, model_id.split('/')[-1])
                yield f"data: {json.dumps({'type': 'member_queued', 'memberId': model_id, 'displayName': display_name})}\n\n"

            async def on_model_streaming(model_id: str, delta_text: str):
                yield f"data: {json.dumps({'type': 'member_streaming', 'memberId': model_id, 'deltaText': delta_text})}\n\n"

            async def on_model_complete(model_id: str, response: str, latency_ms: int):
                yield f"data: {json.dumps({'type': 'member_complete', 'memberId': model_id, 'response': response, 'latencyMs': latency_ms})}\n\n"

            async def on_model_error(model_id: str, message: str):
                yield f"data: {json.dumps({'type': 'member_error', 'memberId': model_id, 'message': message})}\n\n"

            # We need to use a queue to collect SSE events from async callbacks
            event_queue = asyncio.Queue()

            async def queue_event(event_data: str):
                await event_queue.put(event_data)

            async def queued_model_queued(model_id: str):
                display_name = MODEL_DISPLAY_NAMES.get(model_id, model_id.split('/')[-1])
                await queue_event(f"data: {json.dumps({'type': 'member_queued', 'memberId': model_id, 'displayName': display_name})}\n\n")

            async def queued_model_streaming(model_id: str, delta_text: str):
                await queue_event(f"data: {json.dumps({'type': 'member_streaming', 'memberId': model_id, 'deltaText': delta_text})}\n\n")

            async def queued_model_complete(model_id: str, response: str, latency_ms: int):
                await queue_event(f"data: {json.dumps({'type': 'member_complete', 'memberId': model_id, 'response': response, 'latencyMs': latency_ms})}\n\n")

            async def queued_model_error(model_id: str, message: str):
                await queue_event(f"data: {json.dumps({'type': 'member_error', 'memberId': model_id, 'message': message})}\n\n")

            # Run stage 1 in a task and drain events from queue
            stage1_task = asyncio.create_task(
                stage1_collect_responses_stream(
                    request.content,
                    queued_model_queued,
                    queued_model_streaming,
                    queued_model_complete,
                    queued_model_error
                )
            )

            # Drain events while stage 1 runs
            while not stage1_task.done():
                try:
                    event = await asyncio.wait_for(event_queue.get(), timeout=0.1)
                    yield event
                except asyncio.TimeoutError:
                    continue

            # Get remaining events and result
            while not event_queue.empty():
                yield await event_queue.get()

            stage1_results = stage1_task.result()

            # Stage 1 complete event (for legacy compatibility)
            yield f"data: {json.dumps({'type': 'stage1_complete', 'data': stage1_results})}\n\n"

            # Stage 2: Collect rankings
            yield f"data: {json.dumps({'type': 'stage2_start'})}\n\n"

            async def on_stage2_model_complete(model_id: str, parsed_ranking: list):
                await queue_event(f"data: {json.dumps({'type': 'stage2_model_complete', 'memberId': model_id, 'ranking': parsed_ranking})}\n\n")

            stage2_task = asyncio.create_task(
                stage2_collect_rankings_stream(
                    request.content,
                    stage1_results,
                    on_stage2_model_complete
                )
            )

            # Drain events while stage 2 runs
            while not stage2_task.done():
                try:
                    event = await asyncio.wait_for(event_queue.get(), timeout=0.1)
                    yield event
                except asyncio.TimeoutError:
                    continue

            while not event_queue.empty():
                yield await event_queue.get()

            stage2_results, label_to_model = stage2_task.result()
            aggregate_rankings = calculate_aggregate_rankings(stage2_results, label_to_model)

            yield f"data: {json.dumps({'type': 'stage2_complete', 'data': stage2_results, 'metadata': {'label_to_model': label_to_model, 'aggregate_rankings': aggregate_rankings}})}\n\n"

            # Stage 3: Final synthesis with streaming
            yield f"data: {json.dumps({'type': 'final_generating'})}\n\n"

            async def on_final_streaming(delta_text: str):
                await queue_event(f"data: {json.dumps({'type': 'final_streaming', 'deltaText': delta_text})}\n\n")

            stage3_task = asyncio.create_task(
                stage3_synthesize_final_stream(
                    request.content,
                    stage1_results,
                    stage2_results,
                    on_final_streaming
                )
            )

            # Drain events while stage 3 runs
            while not stage3_task.done():
                try:
                    event = await asyncio.wait_for(event_queue.get(), timeout=0.1)
                    yield event
                except asyncio.TimeoutError:
                    continue

            while not event_queue.empty():
                yield await event_queue.get()

            stage3_result = stage3_task.result()

            yield f"data: {json.dumps({'type': 'final_ready', 'content': stage3_result['response'], 'latencyMs': stage3_result.get('latency_ms', 0)})}\n\n"

            # Also emit stage3_complete for legacy compatibility
            yield f"data: {json.dumps({'type': 'stage3_complete', 'data': stage3_result})}\n\n"

            # Wait for title generation
            if title_task:
                title = await title_task
                storage.update_conversation_title(conversation_id, title)
                yield f"data: {json.dumps({'type': 'title_complete', 'data': {'title': title}})}\n\n"

            # Save complete assistant message
            storage.add_assistant_message(
                conversation_id,
                stage1_results,
                stage2_results,
                stage3_result
            )

            yield f"data: {json.dumps({'type': 'complete'})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
