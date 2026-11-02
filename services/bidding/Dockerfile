# Build from the repository root, because this image needs the contracts package:
#   docker build -f services/bidding/Dockerfile -t going-once-bidding .
FROM python:3.12-slim

WORKDIR /code

COPY contracts /code/contracts
COPY services/bidding /code/services/bidding

RUN pip install --no-cache-dir /code/contracts \
    && pip install --no-cache-dir /code/services/bidding

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
