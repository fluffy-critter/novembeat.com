""" Main Publ application """

import logging
import logging.handlers
import os
import os.path
import email.message
import urllib.parse

import werkzeug.exceptions as http_error

import arrow
from flask_hookserver import Hooks
import flask
import publ
import authl.flask
from publ.caching import cache
import publ.config
import slugify
import atomicwrites
from pony import orm
import requests
from bs4 import BeautifulSoup

if os.path.isfile('logging.conf'):
    logging.config.fileConfig('logging.conf')
else:
    try:
        os.makedirs('logs')
    except FileExistsError:
        pass
    logging.basicConfig(level=logging.DEBUG,
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

        'FEDIVERSE_NAME': 'novembeat',
        'FEDIVERSE_HOMEPAGE': 'https://novembeat.com/',

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


def parse_url(url):
    parsed = urllib.parse.urlparse(url)
    if not parsed.scheme:
        parsed = parsed._replace(scheme='https')
    return parsed


@app.route('/_preview')
def generate_preview():
    return generate_iframe(parse_url(flask.request.args['url']))


@cache.memoize()
def generate_iframe(parsed):
    url = parsed.geturl()

    LOGGER.debug("Parsed URL: %s", parsed)
    if parsed.netloc.lower().endswith('youtube.com'):
        # YouTube supports opengraph but it strips out playlists :(
        qs = urllib.parse.parse_qs(parsed.query)
        LOGGER.debug("Parsed query string: %s", qs)
        if 'list' in qs:
            return f'''<iframe width="560" height="315" src="https://www.youtube.com/embed/videoseries?list={qs['list'][0]}" title="YouTube video player" frameborder="0" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture" allowfullscreen seamless></iframe>'''
        if 'v' in qs:
            return f'''<iframe width="560" height="315" src="https://www.youtube.com/embed/{qs['v'][0]}" title="YouTube video player" frameborder="0" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture" allowfullscreen seamless></iframe>'''
        raise http_error.BadRequest("Missing playlist or video ID")

    req = requests.get(parsed.geturl())
    if req.status_code != 200:
        flask.abort(req.status_code)

    if 'audio/' in req.headers['content-type']:
        return f'''<audio src="{url}" type="{req.headers['content-type']}" controls>'''
    if 'video/' in req.headers['content-type']:
        return f'''<video src="{url}" type="{req.headers['content-type']}" controls>'''

    soup = BeautifulSoup(req.text, 'html.parser')

    def find_opengraph(*tags):
        for og_tag in tags:
            node = soup.find('meta', {'property': og_tag, 'content': True})
            if node:
                return node['content']

        return None

    vidurl = find_opengraph('og:video:secure_url',
                            'og:video', 'twitter:player')
    if vidurl:
        width = find_opengraph(
            'og:video:width', 'twitter:player:width') or '100%'
        height = find_opengraph(
            'og:video:height', 'twitter:player:height') or '150'
        desc = find_opengraph('og:title')

        if 'bandcamp.com' in vidurl:
            # bandcamp's player can be a lot better if we override the opengraph
            vidurl = vidurl.replace('/tracklist=false', '').replace('/v=2', '')
            width = '100%'
            height = max(int(height), 400)

        return f'<iframe src="{vidurl}" width="{width}" height="{height}" allow="accelerometer; autoplay; picture-in-picture" seamless><a href="{url}">{desc}</a></iframe>'

    raise http_error.UnsupportedMediaType(
        f"Don't know how to handle URL {url}")


def get_entry_text(form):

    parsed = parse_url(form['entry-url'])
    text = f'''
[Playlist]({parsed.geturl()}):

{generate_iframe(parsed)}
'''

    if 'comment' in form:
        text += f'''
.....

{form['comment']}
'''

    return text


@app.route('/_submit', methods=['POST'])
def submit_entry():
    import publ.user
    import publ.entry

    user = publ.user.get_active()
    if not user:
        raise http_error.Unauthorized()
    if 'banned' in user.groups:
        raise http_error.Forbidden()

    form = flask.request.form

    headers = {}

    headers['Title'] = form['artist-name']
    headers['Author'] = form['artist-name']
    if form.get('artist-url'):
        headers['Artist-URL'] = parse_url(form['artist-url']).geturl()

    headers['Submitter'] = user.identity

    if 'admin' not in user.groups:
        headers['Auth'] = user.identity

    date = arrow.get(tzinfo=config['timezone'])
    year = int(form['year'])
    if year < 2016:
        raise http_error.BadRequest(f"Novembeat wasn't happening in {year}")
    elif year < date.year:
        date = date.replace(year=year)
    elif year > date.year:
        raise http_error.BadRequest(f'{year} is in the future')

    headers['Date'] = date.format()

    body =  get_entry_text(form)

    authorname = slugify.slugify(form['artist-name'])
    filename = os.path.join(
        'works', f'submission-{year} {authorname}.md')
    fullpath = os.path.join(APP_PATH, 'content', filename)

    # See https://github.com/PlaidWeb/Publ/issues/471 for a proposed better way to do this
    try:
        with atomicwrites.atomic_write(fullpath) as file:
            for key, val in headers.items():
                print(f'{key}: {str(val)}', file=file)
            print('', file=file)
            print(body, file=file)
    except FileExistsError:
        raise http_error.Conflict(f"{filename}: file exists")

    for fixup in range(5):
        if publ.entry.scan_file(fullpath, filename, fixup):
            break

    with orm.db_session():
        import publ.model
        entry_record = publ.model.Entry.get(file_path=fullpath)
        entry_obj = publ.entry.Entry.load(entry_record)
        return flask.redirect(entry_obj.archive(paging='year'))
