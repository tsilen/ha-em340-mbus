# Multi-arch base (aarch64 + amd64), Alpine 3.22, pinned release tag.
FROM ghcr.io/home-assistant/base:3.22-2026.10.0

# "~" pins the upstream version but accepts Alpine -rN rebuilds.
RUN apk add --no-cache "python3~3.12" "py3-pyserial~3.5"

COPY em340_mbus.py em340_test.py /app/
COPY run.sh /run.sh
RUN chmod a+x /run.sh && cd /app && python3 em340_test.py

LABEL \
  io.hass.version="0.1.0" \
  io.hass.type="app" \
  io.hass.arch="aarch64|amd64"

CMD [ "/run.sh" ]
