#!/usr/bin/with-contenv bashio
# shellcheck shell=bash
set -e

if ! bashio::services.available "mqtt"; then
    bashio::exit.nok "No MQTT service found. Install and start the Mosquitto broker add-on."
fi

MQTT_HOST="$(bashio::services mqtt 'host')"
MQTT_PORT="$(bashio::services mqtt 'port')"
MQTT_USER="$(bashio::services mqtt 'username')"
MQTT_PASS="$(bashio::services mqtt 'password')"
MBUS_PORT="$(bashio::config 'serial_port')"
MBUS_BAUD="$(bashio::config 'baud_rate')"
MBUS_ADDRESS="$(bashio::config 'address')"
POLL_INTERVAL="$(bashio::config 'poll_interval')"
export MQTT_HOST MQTT_PORT MQTT_USER MQTT_PASS MBUS_PORT MBUS_BAUD MBUS_ADDRESS POLL_INTERVAL

bashio::log.info "Polling M-Bus address ${MBUS_ADDRESS} on ${MBUS_PORT} @ ${MBUS_BAUD} 8E1 every ${POLL_INTERVAL}s (read-only), MQTT ${MQTT_HOST}:${MQTT_PORT}"
exec python3 -u /app/em340_mbus.py
