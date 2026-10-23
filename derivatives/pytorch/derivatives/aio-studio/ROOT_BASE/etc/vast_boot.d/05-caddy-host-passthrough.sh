#!/bin/bash
# Selkies (internal port 16100) checks the browser's Origin against Host itself, so
# Caddy must forward the browser's Host unchanged for it (ADR 0050). Adds the port to
# CADDY_HOST_PASSTHROUGH and keeps any ports the template already listed. Identical in
# every desktop image.
case ",${CADDY_HOST_PASSTHROUGH// /}," in
    *,16100,*) ;;
    *) export CADDY_HOST_PASSTHROUGH="${CADDY_HOST_PASSTHROUGH:+${CADDY_HOST_PASSTHROUGH},}16100" ;;
esac
