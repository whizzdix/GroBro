ARG BUILD_FROM
FROM $BUILD_FROM

RUN apk add --no-cache python3 py3-pip

RUN python3 -m venv /venv

ENV PATH="/venv/bin:$PATH"

WORKDIR /app

COPY grobro/requirements.txt ./requirements.txt

RUN pip install --no-cache-dir -r requirements.txt

WORKDIR /app/grobro
COPY grobro/ /app/grobro/
COPY run.sh /
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
