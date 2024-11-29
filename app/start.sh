#!/bin/bash

source /myenv/bin/activate
while true; do
	if [ -z "${DAV_PORT}" ] && [ -f /data/fast115.sqlite ] && [ -f /data/115-cookies.txt ]; then
		[ -z "${FAST_STRM}" ] && param='-fs' || param=""
		servedb dav -cp /data/115-cookies.txt -f /data/fast115.sqlite \
			$param -P ${DAV_PORT} &> /tmp/dav.log &
	fi
	/app/app.py &> /data/app.log
done
