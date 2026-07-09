FROM python:3.13-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY evm_canon ./evm_canon
RUN pip install --no-cache-dir ".[serve]"

ENV PORT=8080
EXPOSE 8080
CMD ["evm-canon-serve"]
