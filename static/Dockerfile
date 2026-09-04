FROM alpine:latest

RUN apk add --no-cache busybox-extras lowdown

WORKDIR /home/static

COPY *.md ./

COPY imgs/*.png imgs/

RUN for f in *.md; do lowdown -s "$f" -o "${f%.md}.html"; done && \
    chown -R nobody:nobody /home/static

USER nobody

CMD ["httpd", "-f", "-p", "3030", "-h", "/home/static"]
