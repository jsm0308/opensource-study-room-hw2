FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app

ENV HOST=0.0.0.0
ENV PORT=10000
ENV STUDY_DEMO_MODE=1
EXPOSE 10000

CMD ["python", "app/server.py", "--no-browser"]
