FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app app
COPY static static
ENV WIQAYA_ENV=production PYTHONUNBUFFERED=1
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}
