FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY api ./api
COPY tools ./tools
COPY prompts ./prompts
COPY agent_factory.py main.py ./

ENV PYTHONUNBUFFERED=1 \
    LLM_PROVIDER=ollama \
    OLLAMA_MODEL=qwen2.5:3b \
    OLLAMA_BASE_URL=http://ollama:11434 \
    API_BASE_URL=http://127.0.0.1:8765 \
    LLM_TEMPERATURE=0

ENTRYPOINT ["python", "main.py"]
CMD ["создай пользователя с именем Alex"]
