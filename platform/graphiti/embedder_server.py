#!/usr/bin/env python3
"""Minimal OpenAI-compatible FastEmbed endpoint for Graphiti."""

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

from fastembed import TextEmbedding


MODEL_NAME = os.environ.get(
    "FASTEMBED_MODEL",
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
)
MODEL_CACHE = os.environ.get("FASTEMBED_CACHE", "/model-cache/fastembed")
for writable_dir in (
    MODEL_CACHE,
    os.environ.get("HF_HOME", "/model-cache/huggingface"),
    os.environ.get("XDG_CACHE_HOME", "/model-cache/.cache"),
):
    os.makedirs(writable_dir, exist_ok=True)
MODEL = TextEmbedding(model_name=MODEL_NAME, cache_dir=MODEL_CACHE, threads=2)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        if self.path in ("/health", "/healthz"):
            self._json(200, {"status": "ok", "model": MODEL_NAME})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/v1/embeddings":
            self._json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 1_048_576:
                self._json(413, {"error": "invalid request size"})
                return
            payload = json.loads(self.rfile.read(length))
            inputs = payload.get("input")
            if isinstance(inputs, str):
                texts = [inputs]
            elif isinstance(inputs, list) and all(isinstance(item, str) for item in inputs):
                texts = inputs
            else:
                self._json(400, {"error": "input must be a string or a list of strings"})
                return
            if not texts or any(not item.strip() for item in texts):
                self._json(400, {"error": "input must contain non-empty text"})
                return
            vectors = MODEL.embed(texts, batch_size=32)
            data = [
                {"object": "embedding", "index": index, "embedding": vector.tolist()}
                for index, vector in enumerate(vectors)
            ]
            self._json(
                200,
                {
                    "object": "list",
                    "model": MODEL_NAME,
                    "data": data,
                    "usage": {"prompt_tokens": len(texts), "total_tokens": len(texts)},
                },
            )
        except (ValueError, json.JSONDecodeError) as exc:
            self._json(400, {"error": str(exc)})
        except Exception:
            self._json(500, {"error": "embedding request failed"})

    def _json(self, status, payload):
        body = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print(f"graphiti-embedder {self.command} {self.path}", flush=True)


HTTPServer(("0.0.0.0", 8081), Handler).serve_forever()
