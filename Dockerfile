FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /srv/app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

RUN echo 'practice{y3t_4n0th3r_sst1_9}' > /flag.txt && chmod 444 /flag.txt
RUN cp /flag.txt /srv/app/flag.txt && chmod 444 /srv/app/flag.txt
RUN mkdir -p /data

EXPOSE 8000

CMD ["python", "-m", "app.app"]
