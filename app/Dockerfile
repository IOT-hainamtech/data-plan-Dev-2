FROM python:3.12-slim
WORKDIR /app
COPY app/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ ./
COPY tests/ ./tests/
COPY docs/contracts/ ./contracts/
RUN addgroup --system --gid 10001 nextwireless \
    && adduser --system --uid 10001 --ingroup nextwireless --no-create-home nextwireless
ENV PYTHONUNBUFFERED=1
USER 10001:10001
