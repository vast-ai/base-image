"""
GLiNER2 FastAPI Server - entity extraction API with Bearer token authentication.

Environment Variables:
    GLINER_API_KEY: API key for authentication (unsecured if unset)
    GLINER_MODEL:   Model to use (default: fastino/gliner2.5-base-v1)
    GLINER_HOST:    Bind address (default: 127.0.0.1 -- Caddy fronts it)
    GLINER_PORT:    Bind port (default: 18000 -- portal maps 8000 to it)
"""

import os
import time
from typing import Dict, List, Optional

import torch
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from gliner2 import AutoExtractor
from pydantic import BaseModel

# Configuration
MODEL_NAME = os.environ.get("GLINER_MODEL", "fastino/gliner2.5-base-v1")
API_KEY = os.environ.get("GLINER_API_KEY")
HOST = os.environ.get("GLINER_HOST", "127.0.0.1")
PORT = int(os.environ.get("GLINER_PORT", "18000"))

app = FastAPI(
    title="GLiNER2 API",
    description="GPU-accelerated zero-shot entity extraction with GLiNER2",
    version="2.5.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

security = HTTPBearer()

if not API_KEY:
    print("WARNING: GLINER_API_KEY not set. API will be unsecured!")


def verify_token(credentials: HTTPAuthorizationCredentials = Security(security)):
    """Verify the Bearer token"""
    if not API_KEY:
        return True
    if credentials.credentials != API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return True


# Global model state
model = None
device = None


class ExtractRequest(BaseModel):
    text: str
    labels: List[str]
    threshold: Optional[float] = 0.3


class ExtractResponse(BaseModel):
    entities: Dict[str, List[str]]
    inference_time: float
    device: str


class HealthResponse(BaseModel):
    status: str
    model: str
    device: str
    gpu_available: bool
    gpu_name: Optional[str] = None


@app.on_event("startup")
async def load_model():
    """Load the GLiNER2 model on startup.

    AutoExtractor dispatches on the checkpoint's architecture: a 2.5 checkpoint
    yields a BoundaryExtractor, a 2.0 checkpoint a SpanExtractor. Both are
    nn.Module, so .to()/.eval() apply to either, and users pinning GLINER_MODEL
    to a 2.0 checkpoint keep working.
    """
    global model, device

    print(f"Loading GLiNER2 model: {MODEL_NAME}")
    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    print(f"Using device: {device}")

    model = AutoExtractor.from_pretrained(MODEL_NAME)
    model = model.to(device)
    model.eval()

    print(f"Model loaded on {device} ({type(model).__name__})")
    if torch.cuda.is_available():
        gpu_name = torch.cuda.get_device_name(0)
        gpu_memory = torch.cuda.get_device_properties(0).total_memory / 1e9
        print(f"GPU: {gpu_name} ({gpu_memory:.1f} GB)")


@app.get("/health", response_model=HealthResponse)
async def health():
    """Health check endpoint"""
    gpu_name = torch.cuda.get_device_name(0) if torch.cuda.is_available() else None
    return HealthResponse(
        status="running",
        model=MODEL_NAME,
        device=device,
        gpu_available=torch.cuda.is_available(),
        gpu_name=gpu_name,
    )


@app.get("/", response_model=HealthResponse)
async def root():
    """Alias for the health check"""
    return await health()


@app.post("/extract", response_model=ExtractResponse)
async def extract_entities(
    request: ExtractRequest,
    authorized: bool = Depends(verify_token),
):
    """Extract entities from text. Requires Bearer token authentication."""
    try:
        start_time = time.time()
        result = model.extract_entities(
            request.text,
            request.labels,
            threshold=request.threshold,
        )
        inference_time = time.time() - start_time

        return ExtractResponse(
            entities=result.get("entities", {}),
            inference_time=inference_time,
            device=device,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
