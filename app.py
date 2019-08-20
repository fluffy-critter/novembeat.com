""" Main Publ application """

import os
import logging
import logging.handlers
import hmac
import base64

import publ
import flask
import authl.flask

if os.environ.get('FLASK_MEM_PROFILE'):
    import tracemalloc
    tracemalloc.start()

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

APP_PATH = os.path.dirname(os.path.abspath(__file__))

config = {
    'database_config': {
        'provider': 'sqlite',
        'filename': os.path.join(APP_PATH, 'index.db')
    },
    # 'timezone': 'US/Pacific',
    'cache': {
        'CACHE_TYPE': 'memcached',
        'CACHE_DEFAULT_TIMEOUT': 300,
        'CACHE_THRESHOLD': 500,
        'CACHE_KEY_PREFIX': 'beesbuzz.biz',
    } if not os.environ.get('FLASK_DEBUG') else {},

    'index_rescan_interval': 86400,

    'auth': {
        'AUTH_FORCE_SSL': not os.environ.get('FLASK_DEBUG'),

        'SMTP_HOST': 'localhost',
        'SMTP_PORT': 25,
        'EMAIL_FROM': 'nobody@beesbuzz.biz',
        'EMAIL_SUBJECT': 'Sign in to beesbuzz.biz',

        'MASTODON_NAME': 'busybee',
        'MASTODON_HOMEPAGE': 'http://beesbuzz.biz/',

        'INDIEAUTH_CLIENT_ID': authl.flask.client_id,
        # 'INDIELOGIN_CLIENT_ID': 'https://beesbuzz.biz/', # waiting on https://github.com/aaronpk/indielogin.com/issues/38

        'TWITTER_CLIENT_KEY': os.environ.get('TWITTER_CLIENT_KEY'),
        'TWITTER_CLIENT_SECRET': os.environ.get('TWITTER_CLIENT_SECRET'),

        'TEST_ENABLED': os.environ.get('FLASK_DEBUG'),
    }
}

app = publ.Publ(__name__, config)
if not os.path.isfile('.sessionkey'):
    import uuid
    with open('.sessionkey', 'w') as file:
        file.write(str(uuid.uuid4()))
    os.chmod('.sessionkey', 0o600)
with open('.sessionkey') as file:
    app.secret_key = file.read()

def thread_id(item):
    """ Compute an Isso thread URI for the entry """
    if not isinstance(item, publ.entry.Entry):
        raise ValueError("got non-entry object %s" % type(item))

    key = str(item.id)
    tid = hmac.new(b',jvyioG3t3yA@;Uvry=hcTLV',
                   key.encode('utf-8')).hexdigest()[:16]

    return f'/{tid}/{key}'

app.jinja_env.globals.update(thread_id=thread_id)

@app.route('/favicon.ico')
def favicon():
    return flask.redirect(flask.url_for('static', filename='favicon.ico'))


@app.path_alias_regex(r'/d/([0-9]{6,8}(_w)?)\.php')
@app.path_alias_regex(r'/comics/.*/([0-9]{6,8}(_w)?)\.php')
def redirect_date(match):
    ''' legacy comic url '''
    return flask.url_for('category', category='comics', date=match.group(1)), True


@app.path_alias_regex(r'/blog/e/')
def redirect_blog_entry(match):
    ''' missing blog entry -- put up the apology page '''
    return flask.url_for('entry', entry_id=7821), False


@app.path_alias_regex(r'/\.well-known/(host-meta|webfinger).*')
def redirect_bridgy(match):
    ''' support ActivityPub via fed.brid.gy '''
    return 'https://fed.brid.gy' + flask.request.full_path, False


if os.environ.get('FLASK_PROFILE'):
    import flask_profiler
    app.config['flask_profiler'] = {
        'enabled': True,
        'storage': {
            'engine': 'sqlite',
        },
        'ignore': [
            '^/static/.*'
        ]
    }
    flask_profiler.init_app(app)

if os.environ.get('FLASK_MEM_PROFILE'):
    import tracemalloc
    print('enabling memory tracing')

    def show_snapshot(request):
        snapshot = tracemalloc.take_snapshot()
        top_stats = snapshot.statistics('lineno')

        print("[ Top 10 ]")
        for stat in top_stats[:10]:
            print(stat)
        return request
    app.after_request(show_snapshot)

if __name__ == "__main__":

    app.run(port=os.environ.get('PORT', 5000))
