FROM registry.linuxone.cloud.marist.edu/l1cc/jupyterlab-image-s390x:latest
# Standard library only; the Jupyter image is used because python:3.12-slim is not used on this host.
WORKDIR /app
COPY aggregator.py /app/aggregator.py
USER 1000:1000
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
EXPOSE 8000
ENTRYPOINT ["/opt/venv/bin/python", "-I", "/app/aggregator.py"]
