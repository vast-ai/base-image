# GLiNER2 Image

A [GLiNER2](https://github.com/fastino-ai/GLiNER2) image derived from the Vast.ai [PyTorch image](../../README.md). GLiNER2 performs zero-shot entity extraction: entity types are supplied per-request, so no retraining is needed to extract a new type.

This image ships a FastAPI server exposing the model over HTTP, managed as a Supervisor service, along with all features from the Vast.ai base image.

For detailed documentation on Instance Portal, Supervisor, environment variables, and other features, see the [base image README](../../../../README.md).

## Why the server is vendored

Every sibling derivative clones an upstream application repository. GLiNER2 has none to clone: Fastino ships it as a pip library plus HuggingFace weights, and their own serving story is a commercial hosted API. There is no upstream Docker image and no upstream server.

The FastAPI server is therefore vendored in this directory at `ROOT/opt/workspace-internal/gliner/server.py`, rather than cloned during the build.

## Models

`AutoExtractor` dispatches on the checkpoint architecture, so `GLINER_MODEL` accepts both GLiNER 2.5 and GLiNER 2.0 checkpoints.

| Model | Params | Notes |
|-------|--------|-------|
| `fastino/gliner2.5-small-v1` | 73.9M | Smallest |
| `fastino/gliner2.5-base-v1` | 193.6M | **Default** |
| `fastino/gliner2.5-multi-v1` | 287.4M | Multilingual |
| `fastino/gliner2-base-v1` | 205M | Previous generation; loads via `SpanExtractor` |

2.5 checkpoints are self-contained. The 2.0 checkpoint declares an external encoder and pulls a second repository at load time, so it is slower to start.

## Configuration

### Services

| Service | Description |
|---------|-------------|
| `gliner` | FastAPI server (`python server.py`) |

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `WORKSPACE` | `/workspace` | Directory the server is synced to |
| `GLINER_MODEL` | `fastino/gliner2.5-base-v1` | HuggingFace checkpoint to serve |
| `GLINER_API_KEY` | *(unset)* | Bearer token. **If unset the API is unauthenticated** |
| `GLINER_HOST` | `0.0.0.0` | Bind address |
| `GLINER_PORT` | `8000` | Bind port |

Set `HF_TOKEN` if HuggingFace rate-limits anonymous weight downloads.

## API

### `GET /health`

```json
{
  "status": "running",
  "model": "fastino/gliner2.5-base-v1",
  "device": "cuda:0",
  "gpu_available": true,
  "gpu_name": "NVIDIA GeForce RTX 3090"
}
```

### `POST /extract`

Requires `Authorization: Bearer <GLINER_API_KEY>` when the key is set.

```bash
curl -X POST http://<IP>:<PORT>/extract \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $GLINER_API_KEY" \
  -d '{
    "text": "Apple CEO Tim Cook announced iPhone 15 in Cupertino for $999.",
    "labels": ["person", "company", "product", "location", "price"],
    "threshold": 0.3
  }'
```

```json
{
  "entities": {
    "person": ["Tim Cook"],
    "company": ["Apple"],
    "product": ["iPhone 15"],
    "location": ["Cupertino"],
    "price": ["$999"]
  },
  "inference_time": 0.0167,
  "device": "cuda:0"
}
```

## Notes

The DeBERTa-v3 encoder has no SDPA implementation in transformers, so the model runs with eager attention. This is expected and logged at startup as a `RuntimeWarning`; it is not an error.
