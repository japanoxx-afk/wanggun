FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    TZ=Asia/Seoul \
    WANGGUN_DATA_DIR=/data
WORKDIR /app
COPY dummyserver.py .
VOLUME ["/data"]
EXPOSE 9000/tcp 6112/tcp 9000/udp
CMD ["python", "-u", "dummyserver.py"]
