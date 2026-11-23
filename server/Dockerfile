FROM python:3.12-slim
ARG PIP_INDEX=https://pypi.org/simple
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -i ${PIP_INDEX} -r requirements.txt
COPY app.py worker.py ui.html ./
COPY scripts/ ./scripts/
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
