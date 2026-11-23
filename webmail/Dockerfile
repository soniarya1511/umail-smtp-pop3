FROM python:3.12-alpine

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN DJANGO_DEBUG=1 python manage.py collectstatic --noinput \
 && adduser -D -H webmail
USER webmail

ENV PYTHONUNBUFFERED=1
EXPOSE 8000
# One worker on purpose: open SMTP/POP3 sockets live in this process's memory.
CMD ["gunicorn", "umail.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "8"]
