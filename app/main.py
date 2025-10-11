from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

import uvicorn

from app.api import file, magnet
from app.service import open115 as open115_service

# Lifespan to manage 115 tokens
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure the token manager has populated the shared token store before serving traffic
    await open115_service.ensure_tokens_ready()
    try:
        yield
    finally:
        await open115_service.shutdown()

# Create FastAPI instance
app = FastAPI(
    title="Open115 API", description="A FastAPI backend for Open115", version="1.0.0", lifespan=lifespan
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure this properly for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# 115 router
app.include_router(file.router)
app.include_router(magnet.router)


# Routes
@app.get("/")
async def root():
    return {"message": "Welcome to Open115 API"}


@app.get("/health")
async def health_check():
    return {"status": "healthy"}



if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
