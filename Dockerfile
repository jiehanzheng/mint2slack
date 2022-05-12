FROM ghcr.io/mintapi/mintapi@sha256:b1ead691fa28662bd8f7063fbc79c3851ba07d8eb03336630b6a802c224453e5

WORKDIR /app
COPY requirements.txt ./
RUN pip3 install -r requirements.txt
COPY app.py ./

ENV USE_CHROMEDRIVER_ON_PATH=1
ENV PYTHONUNBUFFERED=1

CMD [ "python3", "/app/app.py" ]
