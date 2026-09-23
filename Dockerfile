FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000

WORKDIR /srv

# 应用为纯标准库实现，无第三方依赖
COPY app ./app
COPY tests ./tests

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=3s --retries=3 \
    CMD python3 -c "import json,os,urllib.request; \
port=os.environ.get('PORT','8000'); \
r=urllib.request.urlopen('http://127.0.0.1:%s/healthz'%port,timeout=3); \
assert json.load(r)['status']=='ok'"

CMD ["python3", "-m", "app.server"]
