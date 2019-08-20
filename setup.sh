#!/bin/sh

if ! which pipenv > /dev/null ; then
    echo "Couldn't find pipenv"
    exit 1
fi

echo "Configuring environment..."

pipenv install

# restart the service
killall -HUP gunicorn

echo "Setup complete."
