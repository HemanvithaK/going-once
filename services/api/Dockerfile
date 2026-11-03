# Build from the repository root, because this image needs the contracts package:
#   docker build -f services/api/Dockerfile -t going-once-api .
FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /code

# Contracts first: the API's pyproject depends on it, and it is not on PyPI.
COPY contracts /contracts
RUN pip install --upgrade pip && pip install /contracts

COPY services/api/pyproject.toml ./
RUN pip install -e ".[dev]"

COPY services/api .

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
