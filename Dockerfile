FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY agent.py .
ENV POLL_SECONDS=60
CMD ["python", "agent.py"]
