# Runs the connector unchanged on any container host (Cloud Run, Fly, Railway, Render).
FROM python:3.12-slim
WORKDIR /app
COPY server.py sample_data.py ./
ENV HOST=0.0.0.0 PORT=8787
EXPOSE 8787
CMD ["python", "server.py"]
