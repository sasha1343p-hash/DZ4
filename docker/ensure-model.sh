#!/bin/sh
set -eu
i=0
until ollama list >/dev/null 2>&1; do
  i=$((i + 1))
  if [ "$i" -gt 60 ]; then
    echo "ollama did not become ready" >&2
    exit 1
  fi
  sleep 1
done
if ollama list | grep -q "qwen2.5:3b"; then
  echo "model qwen2.5:3b is already installed"
  exit 0
fi
echo "pulling qwen2.5:3b"
if timeout 40 ollama pull qwen2.5:3b; then
  exit 0
fi
echo "pull failed, trying local GGUF"
bytes=$(wc -c < /model.gguf | tr -d " ")
if [ "$bytes" -lt 1000000 ]; then
  echo "No model. Set GGUF_FILE to a Qwen2.5-3B-Instruct Q4_K_M file, or restore access to registry.ollama.ai." >&2
  exit 1
fi
ollama create qwen2.5:3b -f /Modelfile
