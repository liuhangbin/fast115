#!/bin/bash

source /myenv/bin/activate
while true; do
	/app/app.py &> /data/app.log
done
