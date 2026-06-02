from fastapi import FastAPI

from app.routers.retrieval import router as retrieval_router


def create_app() -> FastAPI:
    app = FastAPI(
        title="TaxHelper Retrieval Service",
        version="0.1.0",
        description="Retrieve relevant legal chunks for answer generation.",
    )
    app.include_router(retrieval_router, prefix="/api/v1")
    return app


app = create_app()
