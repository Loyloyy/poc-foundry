# Private tool-server sibling for the tool-calling pilot. Stdlib-only HTTP server → no pip, tiny image,
# fully offline. The broker runs this as a vetted sibling (image pinned in pipeline.yaml); the emitted
# bundle's compose builds the same source for a human running it standalone.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY app.py /app/app.py
EXPOSE 8000
CMD ["python", "app.py"]
