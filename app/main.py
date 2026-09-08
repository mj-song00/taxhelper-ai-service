import httpx
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator

from app.routers.retrieval import (
    get_chat_job_client,
    get_chunk_search_service,
    get_llm_service,
    get_precedent_search_service,
    get_rabbitmq_publisher,
    router as retrieval_router,
)


class RequestTimingMiddleware:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = uuid4().hex[:8]
        scope.setdefault("state", {})["request_id"] = request_id
        started_at = perf_counter()
        status_code = 500
        response_bytes = 0

        async def send_with_timing(message) -> None:
            nonlocal status_code, response_bytes

            if message["type"] == "http.response.start":
                status_code = message["status"]

            if message["type"] == "http.response.body":
                response_bytes += len(message.get("body", b""))

            await send(message)

            if (
                message["type"] == "http.response.body"
                and not message.get("more_body", False)
            ):
                elapsed_sec = perf_counter() - started_at
                print(
                    f"[HTTP_TIMING] request_id={request_id} phase=response_sent "
                    f"elapsed_sec={elapsed_sec:.3f} elapsed_min={elapsed_sec / 60:.3f} "
                    f"method={scope['method']} path={scope['path']} "
                    f"status_code={status_code} response_bytes={response_bytes}",
                    flush=True,
                )

        try:
            await self.app(scope, receive, send_with_timing)
        except BaseException:
            elapsed_sec = perf_counter() - started_at
            print(
                f"[HTTP_TIMING] request_id={request_id} phase=request_failed "
                f"elapsed_sec={elapsed_sec:.3f} elapsed_min={elapsed_sec / 60:.3f} "
                f"method={scope['method']} path={scope['path']}",
                flush=True,
            )
            raise


async def close_search_clients() -> None:
    if get_chunk_search_service.cache_info().currsize:
        await get_chunk_search_service().client.close()
    if get_precedent_search_service.cache_info().currsize:
        await get_precedent_search_service().client.close()
    if get_llm_service.cache_info().currsize:
        await get_llm_service().close()


def create_app() -> FastAPI:
    app = FastAPI(
        title="TaxHelper Retrieval Service",
        version="0.1.0",
        description="Retrieve relevant legal chunks for answer generation.",
    )
    app.add_middleware(RequestTimingMiddleware)
    app.include_router(retrieval_router, prefix="/api/v1")
    Instrumentator(excluded_handlers=["/metrics"]).instrument(app).expose(
        app, include_in_schema=False
    )

    @app.on_event("shutdown")
    async def shutdown_search_clients() -> None:
        await close_search_clients()
        if get_rabbitmq_publisher.cache_info().currsize:
            await get_rabbitmq_publisher().close()
        if get_chat_job_client.cache_info().currsize:
            await get_chat_job_client().close()

    @app.on_event("startup")
    async def connect_rabbitmq_publisher() -> None:
        try:
            await get_rabbitmq_publisher().connect()
        except Exception as exc:
            # Keep the existing synchronous API available while RabbitMQ is down.
            print(
                f"[RABBITMQ] status=connection_failed "
                f"error={type(exc).__name__}: {exc}",
                flush=True,
            )

    @app.on_event("startup")
    async def warm_up_ollama_model() -> None:
        try:
            await get_llm_service().warm_up()
        except (httpx.HTTPError, ValueError) as exc:
            # Keep the API available when Ollama is temporarily unavailable.
            # The normal request path will surface a useful LLM error response.
            print(
                f"[OLLAMA_WARMUP] status=failed error={type(exc).__name__}: {exc}",
                flush=True,
            )

    return app


app = create_app()
