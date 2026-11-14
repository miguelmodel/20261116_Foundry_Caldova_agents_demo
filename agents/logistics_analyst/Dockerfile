FROM python:3.13-slim

ENV PYTHONNOUSERSITE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --pre -r requirements.txt

COPY main.py erp_tools.py ./
COPY data ./data

EXPOSE 8088
CMD ["python", "main.py"]
