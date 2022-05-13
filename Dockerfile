FROM selenium/standalone-chrome

# add mintapi to path
ENV PATH=$HOME/.local/bin:$PATH

RUN echo "**** install packages ****" && \
    sudo apt-get update && \
    sudo apt-get install -y python3-pip && \
    pip3 install https://github.com/jiehanzheng/mintapi/archive/refs/heads/feature/support-beta.tar.gz && \
    echo "**** cleanup ****" && \
    sudo apt-get clean && \
    sudo rm -rf \
    /tmp/* \
    /var/lib/apt/lists/* \
    /var/tmp/*

WORKDIR /app
RUN pip3 install 'slack_bolt~=1.13.2' 'tinydb~=4.7.0'
COPY app.py ./

ENV USE_CHROMEDRIVER_ON_PATH=1
ENV PYTHONUNBUFFERED=1

CMD [ "python3", "/app/app.py" ]
