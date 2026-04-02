from fastapi import FastAPI
from app.api.v1.routers import payments
from app.core.config import settings


app = FastAPI(title=settings.app_name)

app.include_router(
    payments.router,
    prefix=f"{settings.api_v1_prefix}/payments",
    tags=["payments"]
)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="localhost",
        port=8000,
        reload=settings.debug,
    )
