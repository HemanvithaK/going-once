# Build from the repository root, because this image needs the contracts package:
#   docker build -f services/realtime/Dockerfile -t going-once-realtime .
FROM python:3.12-slim

WORKDIR /code

COPY contracts /code/contracts
COPY services/realtime /code/services/realtime

RUN pip install --no-cache-dir /code/contracts \
    && pip install --no-cache-dir /code/services/realtime

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
