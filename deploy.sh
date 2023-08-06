#!/bin/sh
# wrapper script to pull the latest site content and redeploy

cd $(dirname $0)
export PATH=$PATH:$HOME/.poetry/bin

# see where in the history we are now
PREV=$(git rev-parse --short HEAD)

git pull --ff-only || exit 1

disposition=reload

if git diff --name-only $PREV | grep -qE '^(templates/|app\.py)' ; then
    echo "Configuration or template change detected"
    disposition=restart
fi

if git diff --name-only $PREV | grep -q poetry.lock ; then
    echo "poetry.lock changed"
    poetry install || exit 1
    disposition=restart
fi

if [ "$1" != "nokill" ] && [ ! -z "$disposition" ] ; then
    systemctl --user $disposition novembeat.com
fi

echo "Updating the content index..."
poetry run flask publ reindex

count=0
while [ $count -lt 5 ] && [ ! -S $HOME/.vhosts/novembeat.com ] ; do
    count=$(($count + 1))
    echo "Waiting for service to restart... ($count)"
    sleep $count
done

echo "Sending push notifications"
poetry run pushl -rvvkc $HOME/var/pushl \
    https://novembeat.com/feed \
    http://novembeat.com/feed

