""" Main Publ application """
# pylint:disable=missing-function-docstring,import-outside-toplevel

import logging
import logging.handlers
import os
import os.path
import email.message
import re
import time
import urllib.parse

import werkzeug.exceptions as http_error

import arrow
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
from flask_github_webhook import GithubWebhook

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
    'index_enable_watchdog': bool(os.environ.get('FLASK_DEBUG')),

    'auth': {
        'AUTH_FORCE_HTTPS': not os.environ.get('FLASK_DEBUG'),

        'SMTP_HOST': 'localhost',
        'SMTP_PORT': 25,
        'EMAIL_FROM': 'nobody@novembeat.com',
        'EMAIL_SUBJECT': 'Sign in to novembeat.com',

        'FEDIVERSE_NAME': 'novembeat',
        'FEDIVERSE_HOMEPAGE': 'https://novembeat.com/',

        'INDIEAUTH_CLIENT_ID': authl.flask.client_id,

        'TWITTER_CLIENT_KEY': os.environ.get('TWITTER_CLIENT_KEY'),
        'TWITTER_CLIENT_SECRET': os.environ.get('TWITTER_CLIENT_SECRET'),

        'TEST_ENABLED': os.environ.get('FLASK_DEBUG'),
    },

    'auth_log_prune_age': 86400 * 90,
}

if not os.path.isfile('.sessionkey'):
    import uuid
    with open('.sessionkey', 'w', encoding='utf-8') as file:
        file.write(str(uuid.uuid4()))
    os.chmod('.sessionkey', 0o600)
with open('.sessionkey', encoding='utf-8') as file:
    config['secret_key'] = file.read()

app = publ.Publ(__name__, config)
app.config['GITHUB_WEBHOOK_ENDPOINT'] = '/_gh'
app.config['GITHUB_WEBHOOK_SECRET'] = os.environ.get('GITHUB_SECRET')

@app.route('/favicon.ico')
def favicon():
    return flask.redirect(flask.url_for('static', filename='favicon.ico'))


hooks = GithubWebhook(app)


@hooks.hook()
def deploy(data):
    import threading
    import subprocess

    LOGGER.info("Got github hook with data: %s", data)

    try:
        result = subprocess.check_output(
            ['./deploy.sh', 'nokill'],
            stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as err:
        LOGGER.error("Deployment failed: %s", err.output)
        return flask.Response(err.output, status=500, mimetype='text/plain')

    def restart_server():
        LOGGER.info("Restarting")
        subprocess.run(['systemctl', '--user', 'restart',
                       'novembeat.com'], check=True)

    LOGGER.info("Restarting server in 3 seconds...")
    threading.Timer(3, restart_server).start()

    return flask.Response(result, mimetype='text/plain')


def parse_url(url):
    if '//' not in url:
        url = f'https://{url}'
    parsed = urllib.parse.urlparse(url)
    if not parsed.scheme:
        parsed = parsed._replace(scheme='https')
    return parsed


@app.route('/_preview')
def generate_preview():
    text, url, desc = generate_iframe(parse_url(flask.request.args['url']))
    try:
        return f'''
<!-- {url} -->
<p><a href="{url}">{desc}</a>:</p>
{text}
'''
    except http_error.HTTPException as error:
        return str(error), error.code


@cache.memoize()
def generate_iframe(parsed):
    # pylint:disable=line-too-long
    url = parsed.geturl()

    output = BeautifulSoup(f'''<iframe frameborder="0" width="100%"
        allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
        allowfullscreen seamless><a href="{url}">Play album</a></iframe>
        <audio controls><a href="{url}">Play audio</a></audio>
        <video controls><a href="{url}">Play video</a></video>''', 'html.parser')

    LOGGER.debug("Parsed URL: %s", parsed)
    if parsed.netloc.lower().endswith('youtube.com'):
        # YouTube supports opengraph but it strips out playlists :(
        # pylint:disable=invalid-name
        qs = urllib.parse.parse_qs(parsed.query)
        LOGGER.debug("Parsed query string: %s", qs)
        output.iframe['width'] = 560
        output.iframe['height'] = 315
        if 'list' in qs:
            output.iframe['src'] = f"https://www.youtube.com/embed/videoseries?list={qs['list'][0]}"
            output.iframe.a.replace_with = "YouTube playlist"
            return output.iframe, f"https://youtube.com/playlist?list={qs['list'][0]}", "YouTube playlist"
        if 'v' in qs:
            output.iframe['src'] = f"https://www.youtube.com/embed/{qs['v'][0]}"
            output.iframe.a.replace_with = "YouTube video"
            return output.iframe, f"https://www.youtube.com/embed/{qs['v'][0]}", "YouTube video"
        raise http_error.BadRequest("Missing playlist or video ID")

    try:
        LOGGER.info("Retrieving %s", parsed.geturl())
        req = requests.head(parsed.geturl(), timeout=3, allow_redirects=True)
        if req.status_code != 200:
            flask.abort(req.status_code)
    except IOError as error:
        raise http_error.BadRequest(
            f"Unable to retrieve preview for {url}: {error}")

    if 'audio/' in req.headers['content-type']:
        output.audio['src'] = url
        output.audio['type'] = req.headers['content-type']
        return output.audio, url, "Audio file"
    if 'video/' in req.headers['content-type']:
        output.video['src'] = url
        output.video['type'] = req.headers['content-type']
        return output.video, url, "Video file"

    LOGGER.info("Parsing the page")
    try:
        LOGGER.info("Retrieving %s", parsed.geturl())
        req = requests.get(parsed.geturl(), timeout=3, allow_redirects=True)
        if req.status_code != 200:
            flask.abort(req.status_code)
    except IOError as error:
        raise http_error.BadRequest(
            f"Unable to retrieve preview for {url}: {error}")

    soup = BeautifulSoup(req.text, 'html.parser')

    def find_opengraph(*tags):
        for og_tag in tags:
            if node := soup.find('meta', {'property': og_tag, 'content': True}):
                return node['content']

        return None

    title = find_opengraph('og:title') or soup.title.text or 'album'
    LOGGER.info("Title: %s", title)

    if soup.find('meta', {'name': 'itch:path', 'content': True}):
        if node := soup.find('iframe', {'id': 'game_drop'}):
            LOGGER.info("found itch.io game %s", node['src'])
            if match := re.search('html/([0-9]*)-', node['src']):
                itch_id = match.group(1)
                LOGGER.info("itch_id %s", itch_id)
                output.iframe['src'] = f'https://itch.io/embed-upload/{itch_id}?color=333333'
                output.iframe['height'] = 480
                output.iframe.a.replace_with = f'Play {title} on itch.io'
                return output.iframe, url, f'{title}'
        raise http_error.BadRequest("Itch game doesn't support embedding")

    vidurl = find_opengraph('og:video:secure_url',
                            'og:video', 'twitter:player')
    if vidurl:
        width = find_opengraph(
            'og:video:width', 'twitter:player:width') or '100%'
        height = find_opengraph(
            'og:video:height', 'twitter:player:height') or '150'

        if 'bandcamp.com' in vidurl:
            # bandcamp's player can be a lot better if we override the opengraph
            vidurl = vidurl.replace('/tracklist=false', '').replace('/v=2', '')
            width = '100%'
            if 'album=' in vidurl:
                height = max(int(height), 400)

        output.iframe['src'] = vidurl
        output.iframe['width'] = width
        output.iframe['height'] = height
        output.iframe.a.replace_with = title
        return output.iframe, re.sub('^www.', '', urllib.parse.urlparse(vidurl).netloc), title

    raise http_error.UnsupportedMediaType(
        f"Don't know how to handle URL {url}")


def get_entry_text(form, playlists):

    text = ''
    for url, embed_text, desc in playlists:
        text += f'<!-- {url} -->\n[{desc}]({url}):\n\n{embed_text}\n'

    if 'comment' in form:
        text += f'''
.....

{form['comment']}
'''

    return text


@app.route('/_submit', methods=['POST'])
def submit_entry():
    # pylint:disable=too-many-locals,too-many-branches,too-many-statements

    user = publ.user.get_active()
    if not user:
        raise http_error.Unauthorized()
    if 'banned' in user.groups:
        raise http_error.Forbidden()

    form = flask.request.form

    headers = {}

    artistname = form.get('artist-name').strip()
    if not artistname:
        raise http_error.BadRequest('Missing artist name')

    headers['Title'] = artistname
    headers['Author'] = artistname
    if form.get('artist-url'):
        headers['Artist-URL'] = parse_url(form['artist-url']).geturl()

    if user.identity.startswith('mailto:'):
        headers['Submitter'] = user.identity.replace('mailto:', '').replace('@', ' - AT - ').replace('.', ' - DOT - ')
    else:
        headers['Submitter'] = user.identity

    if 'admin' not in user.groups:
        headers['Auth'] = user.identity

    date = arrow.get(tzinfo=config['timezone'])
    year = int(form['year'])
    if year < 2016:
        raise http_error.BadRequest(f"Novembeat wasn't happening in {year}")
    if year < date.year:
        date = date.replace(year=year)
    elif (year, 11) > (date.year, date.month):
        raise http_error.BadRequest(f'November {year} is in the future')

    headers['Date'] = date.format()

    playlists = []
    domains = set()
    for field in ('entry-url', 'alternate-url'):
        url = form.get(field)
        if url:
            embed_text, domain, desc = generate_iframe(parse_url(url))
            if domain:
                if domain not in domains:
                    playlists.append((url, embed_text, desc))
                    domains.add(domain)
                else:
                    raise http_error.BadRequest(
                        f'Got multiple URLs for {domain}')
    if not playlists:
        raise http_error.BadRequest("No playlist provided")

    body = get_entry_text(form, playlists)

    authorname = slugify.slugify(f'{user.humanize} {artistname}')
    filename = os.path.join(
        'works', f'{year}-{authorname}.md')
    fullpath = os.path.join(APP_PATH, 'content', filename)

    # See https://github.com/PlaidWeb/Publ/issues/471 for a proposed better way to do this
    try:
        with atomicwrites.atomic_write(fullpath, overwrite=True) as outfile:
            for key, val in headers.items():
                print(f'{key}: {str(val)}', file=outfile)
            print('', file=outfile)
            print(body, file=outfile)
    except FileExistsError as exc:
        raise http_error.Conflict(f"{filename}: file exists") from exc

    flask.current_app.indexer.scan_file(fullpath, filename, 0)

    while flask.current_app.indexer.in_progress:
        time.sleep(0.5)
    LOGGER.info("file %s scanned", filename)

    with orm.db_session():
        entry_record = publ.model.Entry.get(file_path=fullpath)
        LOGGER.info("Sending admin email for %s", entry_obj)

        if entry_record:
            entry_obj = publ.entry.Entry.load(entry_record)
            send_admin_mail(entry_obj)
            return flask.redirect(entry_obj.archive(paging='year'))
        return flask.redirect(publ.category.Category.load('works').link(date=year))


def send_admin_mail(entry_obj):
    from authl.handlers.email_addr import smtplib_connector, simple_sendmail

    if not os.environ.get('ADMIN_EMAIL'):
        LOGGER.warning("Admin email is unset")
        return

    connector = smtplib_connector(
        hostname='localhost',
        port=25,
    )
    send_func = simple_sendmail(connector, 'submissions@novembeat.com',
                                f"New Novembeat entry: {entry_obj.title}")

    msg = email.message.EmailMessage()
    msg['To'] = os.environ.get('ADMIN_EMAIL')

    msg.set_content(f'''
The following entry was just submitted on novembeat.com:

Artist: {entry_obj.get('Title')}
Year: {entry_obj.date.format('YYYY')}
Entry: {entry_obj.link(absolute=True)}
Filename: {entry_obj.file_path}
''')

    LOGGER.info("Sending email to %s", os.environ.get('ADMIN_EMAIL'))
    send_func(msg)
    LOGGER.info("Sent?")

