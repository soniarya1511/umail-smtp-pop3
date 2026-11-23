FROM python:3.12-alpine

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY mailserver ./mailserver

# Run unprivileged; compose lowers ip_unprivileged_port_start so port 25 still binds.
RUN adduser -D -H mail-svc
USER mail-svc

ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python", "-m", "mailserver"]
