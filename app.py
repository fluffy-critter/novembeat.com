""" Main Publ application """

import logging
import logging.handlers
import os

from flask_hookserver import Hooks
import flask
import publ
import authl.flask

if os.path.isfile('logging.conf'):
    logging.config.fileConfig('logging.conf')
else:
    try:
        os.makedirs('logs')
    except FileExistsError:
        pass
    logging.basicConfig(level=logging.INFO,
                        handlers=[
                            logging.handlers.TimedRotatingFileHandler(
                                'logs/publ.log', when='D'),
                            logging.StreamHandler()
                        ],
                        format="%(levelname)s:%(threadName)s:%(name)s:%(message)s")

LOGGER = logging.getLogger(__name__)
LOGGER.info("Setting up")


APP_PATH = os.path.dirname(os.path.abspath(__file__))

config = {
    'database_config': {
        'provider': 'sqlite',
        'filename': os.path.join(APP_PATH, 'index.db')
    },
    'timezone': 'US/Pacific',
    'cache': {
        'CACHE_TYPE': 'memcached',
        'CACHE_DEFAULT_TIMEOUT': 86413,
        'CACHE_THRESHOLD': 500,
        'CACHE_KEY_PREFIX': 'novembeat.com',
    } if not os.environ.get('FLASK_DEBUG') else {},

    'index_rescan_interval': 86400,

    'auth': {
        'AUTH_FORCE_HTTPS': not os.environ.get('FLASK_DEBUG'),

        'SMTP_HOST': 'localhost',
        'SMTP_PORT': 25,
        'EMAIL_FROM': 'nobody@beesbuzz.biz',
        'EMAIL_SUBJECT': 'Sign in to beesbuzz.biz',

        'FEDIVERSE_NAME': 'busybee',
        'FEDIVERSE_HOMEPAGE': 'https://beesbuzz.biz/',

        'INDIEAUTH_CLIENT_ID': authl.flask.client_id,

        'TWITTER_CLIENT_KEY': os.environ.get('TWITTER_CLIENT_KEY'),
        'TWITTER_CLIENT_SECRET': os.environ.get('TWITTER_CLIENT_SECRET'),

        'TEST_ENABLED': os.environ.get('FLASK_DEBUG'),
    },

    'auth_log_prune_age': 86400 * 90,
}

app = publ.Publ(__name__, config)
app.config['GITHUB_WEBHOOKS_KEY'] = os.environ.get('GITHUB_SECRET')
app.config['VALIDATE_IP'] = False

if not os.path.isfile('.sessionkey'):
    import uuid
    with open('.sessionkey', 'w') as file:
        file.write(str(uuid.uuid4()))
    os.chmod('.sessionkey', 0o600)
with open('.sessionkey') as file:
    app.secret_key = file.read()


@app.route('/favicon.ico')
def favicon():
    return flask.redirect(flask.url_for('static', filename='favicon.ico'))

hooks = Hooks(app, url='/_gh')

@hooks.hook('push')
def deploy(data, delivery):
    import threading
    import werkzeug.exceptions as http_error
    import subprocess
    import flask

    try:
        result = subprocess.check_output(
            ['./deploy.sh', 'nokill'],
            stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as err:
        LOGGER.error("Deployment failed: %s", err.output)
        return flask.Response(err.output, status_code=500, mimetype='text/plain')

    def restart_server(pid):
        LOGGER.info("Restarting")
        os.kill(pid, signal.SIGHUP)

    LOGGER.info("Restarting server in 3 seconds...")
    threading.Timer(3, restart_server, args=[os.getpid()]).start()

    return flask.Response(result, mimetype='text/plain')
