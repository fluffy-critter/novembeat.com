""" Main Publ application """

import flask
import logging
import logging.handlers
import os

import publ

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

logging.info("Setting up")

LOGGER=logging.getLogger(__name__)

APP_PATH = os.path.dirname(os.path.abspath(__file__))

config = {
    'database_config': {
        'provider': 'sqlite',
        'filename': os.path.join(APP_PATH, 'index.db')
    },
    'timezone': 'US/Pacific',
    'cache': {
        'CACHE_TYPE': 'memcached',
        'CACHE_DEFAULT_TIMEOUT': 300,
        'CACHE_THRESHOLD': 500,
        'CACHE_KEY_PREFIX': 'novembeat.com',
    } if not os.environ.get('FLASK_DEBUG') else {},

    'index_rescan_interval': 86400,
}

app = publ.Publ(__name__, config)


@app.route('/favicon.ico')
def favicon():
    return flask.redirect(flask.url_for('static', filename='favicon.ico'))


@app.route('/_deploy', methods=['POST'])
def deploy():
    import threading
    import hmac
  
    data=flask.request.get_data()
    LOGGER.info("Got deployment request: %r %s X-Hub-Signature=%s",
                flask.request.form,
                data,
                flask.request.headers.get('X-Hub-Signature'))
    
    digest = hmac.new(os.environ.get('GITHUB_SECRET'),
                      data,
                      digestmod='sha1')
    if not hmac.compare_digest(digest.hexdigest(),
                               flask.request.headers.get('X-Hub-Signature')):
        raise http_error.Forbidden()

    try:
        result = subprocess.check_output(
            ['./deploy.sh', 'nokill'],
            stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as err:
        logging.error("Deployment failed: %s", err.output)
        return flask.Response(err.output, status_code=500, mimetype='text/plain')

    def restart_server(pid):
        logging.info("Restarting")
        os.kill(pid, signal.SIGHUP)

    logging.info("Restarting server in 3 seconds...")
    threading.Timer(3, restart_server, args=[os.getpid()]).start()

    return flask.Response(result, mimetype='text/plain')

