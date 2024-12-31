#!/bin/bash

source /myenv/bin/activate
while true; do
	if [ -n "${EMBY_HOST}" ] && [ -n "${EMBY_PROXY_PORT}" ]; then
		emby-proxy -P ${EMBY_PROXY_PORT} ${EMBY_HOST} &> /data/emby.log &
	fi
	if [ -n "${DAV_PORT}" ] && [ -f /data/fast115.sqlite ] && [ -f /data/115-cookies.txt ]; then
		param="-P ${DAV_PORT}"
		[ -n "${FAST_STRM}" ] && param="${param} -fs"
		[ -n "${STRM_HOST}" ] && param="${param} -o ${STRM_HOST}"
		servedb dav -cp /data/115-cookies.txt -f /data/fast115.sqlite $param &> /data/dav.log &
	fi
	/app/app.py &> /data/app.log
	sleep 10
done
