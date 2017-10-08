#!/usr/bin/python2.7

import collections
import datetime
import errno
import logging
import os
import pipes
import shutil
import string
import subprocess
import sys
import textwrap
import time

import flask
import marshmallow

from flask import Flask, redirect, url_for, request, render_template
from marshmallow import ValidationError
from werkzeug.exceptions import NotFound, BadRequestKeyError
from werkzeug.routing import UnicodeConverter


# TODO: Make all filesystem paths absolute or use chdir.


class Utf8Response(flask.Response):
    charset = 'utf-8'


app = Flask(__name__)
app.response_class = Utf8Response

def set_app_secret_key(app, filename='secret_key'):
    """Configure the SECRET_KEY from a file in the instance directory,
    or on failure, exit printing the instructions for creating it.

    From <http://flask.pocoo.org/snippets/104/>.
    """
    filename = os.path.abspath(os.path.join(app.instance_path, filename))
    try:
        app.config['SECRET_KEY'] = open(filename, 'rb').read()
    except IOError as exc:
        if exc.errno != errno.ENOENT:
            raise
        msg = 'Error: Failed to read secret key. Create it with:\n'
        if not os.path.isdir(os.path.dirname(filename)):
            msg += 'mkdir -p %s\n' % pipes.quote(os.path.dirname(filename))
            msg += 'chmod 0700 %s\n' % pipes.quote(os.path.dirname(filename))
        msg += 'head -c 32 /dev/urandom > %s\n' % pipes.quote(filename)
        sys.exit(msg)

set_app_secret_key(app)


@app.errorhandler(400)
def error_bad_request(exc):
    if isinstance(exc, BadRequestKeyError):
        (key,) = exc.args
        return http_error(400, "Missing key: %s" % (key,))
    else:
        return http_error(400, "Bad request")


@app.errorhandler(404)
def error_not_found(exc):
    if isinstance(exc, BadPageName):
        return http_error(404, "Bad page name '%s'" % (exc.bad_name,))
    else:
        return http_error(404, "Not found")


def read_file(filename):
    """Reads the named file and returns its contents as a (byte) string."""
    with open(filename, "rb") as f:
        return f.read()


def write_file(filename, data):
    """Writes a (byte) string of data to the named file."""
    with open(filename, "wb") as f:
        f.write(data)


def read_utf8(filename):
    """Reads the named file and returns its UTF-8 contents as unicode."""
    return read_file(filename).decode('utf-8')


def write_utf8(filename, text):
    """Writes a unicode string of text to the named file as UTF-8."""
    write_file(filename, text.encode('utf-8'))


def http_error(code, message):
    """
    Returns an error page containing 'message' with HTTP response code 'code'.
    """
    return Utf8Response(render_template("error.html", message=message), code)


class SaveChangesSchema(marshmallow.Schema):
    class Meta:
        ordered = True  # Needed so list(schema.fields) is ordered.

    name = marshmallow.fields.String()
    width = marshmallow.fields.Integer(required=True)
    height = marshmallow.fields.Integer(required=True)
    msPerFrame = marshmallow.fields.Integer(required=True)
    debug = marshmallow.fields.Boolean(missing=False)
    source = marshmallow.fields.String(required=True)
    newpage = marshmallow.fields.String(required=True)
    upload = marshmallow.fields.Boolean(missing=False)

    debug.truthy = ['on']
    debug.falsy = ['off']
    upload.truthy = ['Upload']

    def __init__(self, pagename, strict=False):
        super(SaveChangesSchema, self).__init__(strict=strict)
        self.pagename = pagename

    @marshmallow.validates('newpage')
    def validate_newpage(self, newpage):
        if not is_valid_page_name(newpage):
            raise ValidationError(
                "Your changes have not been saved because '%s' is "
                "not allowed as a page name. Page names must start with a "
                "letter, must contain only letters and digits, must not be "
                "entirely capital letters, and must have at least three "
                "characters and at most twenty." % newpage)  # TODO: >20?
        if newpage != self.pagename and os.path.isdir("wiki/%s/" % newpage):
            raise ValidationError(
                "Your changes have not been saved because a page called "
                "'%s' already exists." % newpage)

    @staticmethod
    def validate_uploadedImage():
        if not request.files.get('uploadedImage'):
            raise ValidationError("Upload file not provided.")
        image_data = request.files['uploadedImage'].read(102400)
        if len(image_data) < 8 or image_data[:8] != b'\x89PNG\r\n\x1a\n':
            raise ValidationError("Images must be in PNG format.")
        if len(image_data) >= 102400:
            raise ValidationError("Image files must be smaller than 100K.")
        return image_data

    @staticmethod
    def first_error(form):
        if any(x in form.errors for x in ['width', 'height', 'msPerFrame']):
            return "The width, height and frame rate must all be integers."
        for field in SaveChangesSchema.FIELDS:
            if field in form.errors:
                error_msg = next(iter(form.errors[field]))
                return '{0}: {1}'.format(field, error_msg)
        if 'uploadedImage' in form.errors:
            return next(iter(form.errors['uploadedImage']))

SaveChangesSchema.FIELDS = list(SaveChangesSchema('dummy').fields)


class GamePropertiesSchema(marshmallow.Schema):
    class Meta:
        ordered = True

    name = marshmallow.fields.String()
    width = marshmallow.fields.Integer(missing=256)
    height = marshmallow.fields.Integer(missing=256)
    msPerFrame = marshmallow.fields.Integer(missing=40)
    debug = marshmallow.fields.Boolean(missing=False)

    debug.truthy = ['true']
    debug.falsy = ['false']

    @marshmallow.post_load
    def make_props(self, data):
        return GameProperties(**data)


class GameProperties(collections.namedtuple('GameProperties', '''
    name
    width
    height
    msPerFrame
    debug
''')):
    @staticmethod
    def load(filename):
        """
        Parses a file of the form of "properties.txt" and returns its contents
        as a GameProperties. This is used, for example, to retrieve the width
        and height of a game for inclusion in the applet tag.
        """
        data = {}
        for line in read_utf8(filename).split("\n"):
            line = line.strip()
            if line and not line.startswith('#'):
                colon = line.find(":")
                if colon == -1:
                    raise ValueError("Colon missing from '{0}'".format(line))
                key, value = line[:colon], line[colon + 1:]
                data[key] = value.strip()
        return GamePropertiesSchema(strict=True).load(data).data

    def save(self, filename):
        file_contents = (
            'name: {name}\n'
            'width: {width}\n'
            'height: {height}\n'
            'msPerFrame: {msPerFrame}\n'
            'debug: {debug}\n'
        ).format(
            name=self.name,
            width=self.width,
            height=self.height,
            msPerFrame=self.msPerFrame,
            debug='true' if self.debug else 'false',
        )
        write_utf8(filename, file_contents)


def check_save_changes_form(pagename):
    schema = SaveChangesSchema(pagename, strict=False)
    form = schema.load(request.form)
    if not form.errors and form.data['upload']:
        try:
            form.data['image_data'] = schema.validate_uploadedImage()
        except ValidationError as e:
            form.errors['uploadedImage'] = [e]
    if form.errors:
        props = None
    else:
        props = GameProperties(
            name=form.data['name'],
            width=form.data['width'],
            height=form.data['height'],
            msPerFrame=form.data['msPerFrame'],
            debug=form.data.get('debug') or False)
    return (form, props)


def is_valid_page_name(pagename):
    """
    Page names must start with a letter, must contain only letters and
    digits, must not be entirely capital letters, and must have at least three
    characters and at most twenty.
    """

    def all_alphanumeric(s):
        alphanumerics = string.ascii_letters + string.digits
        return all(c in alphanumerics for c in s)

    def all_uppercase(s):
        return all(c in string.ascii_uppercase for c in s)

    if not (3 <= len(pagename) <= 20): return False
    if pagename[0] not in string.ascii_letters: return False
    if not all_alphanumeric(pagename): return False
    if all_uppercase(pagename): return False
    return True


class BadPageName(NotFound):
    def __init__(self, bad_name):
        super(BadPageName, self).__init__("Bad page name")
        self.bad_name = bad_name


class PageNameConverter(UnicodeConverter):
    def to_python(self, value):
        if is_valid_page_name(value):
            return value
        else:
            raise BadPageName(value)


app.url_map.converters['PageName'] = PageNameConverter


@app.route('/')
def index():
    return render_template("welcome-to-nifki.html")


@app.route('/pages/')
def pages_index():
    return render_template(
        "list-of-all-pages.html",
        pagenames=sorted([
            page for page in os.listdir("wiki") if is_valid_page_name(page)
        ]))


@app.route('/pages/<PageName:pagename>/')
def page_index(pagename):
    return redirect("/pages/%s/play/" % pagename)


@app.route('/pages/<PageName:pagename>/res/<PageName:filename>')
def page_resource(pagename, filename):
    data = read_file("wiki/%s/res/%s" % (pagename, filename))
    return flask.Response(data, 200, content_type="image/png")


# We don't need downloadable jars any more!
'''
@app.route('/pages/<PageName:pagename>/<_anything>.jar')
def jar(pagename, _anything):
    """
    Returns the jar file for this page. Note that the requested filename is
    completely ignored. In fact, we vary the filename in order to defeat the
    browser cache.
    """
    jarfile = read_file("wiki/nifki-out/%s.jar" % (pagename,))
    response = flask.Response(
        jarfile, 200, {"Content-Type": "application/java-archive"}
    )
    # Mozilla refuses to cache anything without a "Last-Modified" header,
    # and ludicrously downloads a copy of the jar file for every entry
    # contained within it. Really! Top quality!
    now = datetime.datetime.now()
    response.headers["Last-Modified"] = response.headers["Date"] = now
    return response
'''


@app.route('/pages/<PageName:pagename>/play/')
def play(pagename):
    """
    Returns the page with the applet tag on it, if the game compiled
    successfully, otherwise returns a page showing the compiler output,
    or redirects to the edit page if the compiler hasn't run for this game.
    """
    if os.path.exists("wiki/nifki-out/%s.jar" % pagename):
        props = GameProperties.load("wiki/%s/properties.txt" % pagename)
        return render_template("playing.html",
            pagename=pagename,
            width=props.width,
            height=props.height,
            random=int(time.time()),
            name=props.name)
    elif os.path.exists("wiki/nifki-out/%s.err" % pagename):
        err = read_utf8("wiki/nifki-out/%s.err" % pagename)
        lines = []
        for line in err.split("\n"):
            for shortline in textwrap.wrap(line, width=80):
                lines.append(shortline)
        err = "\n".join(lines)
        return render_template(
            "compiler-output.html", pagename=pagename, err=err)
    else:
        return redirect(url_for('edit', pagename=pagename))


@app.route('/pages/<PageName:pagename>/edit/')
def edit(pagename):
    if not os.path.isdir("wiki/%s/" % pagename):
        return render_template("no-such-page.html", pagename=pagename)
    # Load "source.sss" file.
    source = read_utf8("wiki/%s/source.sss" % pagename)
    # Load "properties.txt" file.
    props = GameProperties.load("wiki/%s/properties.txt" % pagename)
    # Return an editing page.
    form = SaveChangesSchema(pagename, strict=True).load({
        'newpage': pagename,
        'source': source,
        'name': props.name,
        'width': props.width,
        'height': props.height,
        'msPerFrame': props.msPerFrame,
        'debug': 'on' if props.debug else 'off',
    })
    return edit_page(pagename, form)


def edit_page(pagename, form):
    """
    Returns an edit page populated with the specified data.
    """
    return render_template(
        "editing.html",
        pagename=pagename,
        error_message=SaveChangesSchema.first_error(form) or '',
        image_list=sorted(os.listdir("wiki/%s/res" % pagename)),
        form=form.data,
        uploadedImage='')


@app.route('/pages/<PageName:pagename>/save/', methods=['POST'])
def save(pagename):
    form, props = check_save_changes_form(pagename)
    newpage = form.data['newpage']
    source = form.data['source']
    if props is None:
        return edit_page(pagename, form)
    upload = form.data.get('upload', False)
    assert upload is True or upload is False
    if upload:
        return upload_image(pagename, form, props)
    # Ok to save under the new name (`newpage`).
    if newpage != pagename:
        # New page.
        shutil.copytree("wiki/%s/" % pagename, "wiki/%s/" % newpage)
    # Save the source file, 'source.sss'.
    write_utf8("wiki/%s/source.sss" % newpage, source)
    # Save the properties file, 'properties.txt'.
    props.save("wiki/%s/properties.txt" % newpage)
    # Run the compiler. We capture its output to avoid writing to stdout:
    # http://blog.dscpl.com.au/2009/04/wsgi-and-printing-to-standard-output.html
    p = subprocess.Popen(
        "java -jar compiler.jar wiki %s" % newpage,
        shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    compiler_stdout, compiler_stderr = p.communicate()
    app.logger.info(compiler_stdout)  # "Wrote somefile."
    if compiler_stderr:
        # This is for actually-didn't-run errors, not source code errors.
        app.logger.error(compiler_stderr)
    if p.returncode != 0:
        return Utf8Response(render_template("compiler-error.html"), 500)
    else:
        return redirect(url_for('play', pagename=newpage))


def upload_image(pagename, form, props):
    if props is not None:
        assert not SaveChangesSchema.first_error(form)
        filename = request.files['uploadedImage'].filename
        filename = get_new_resource_name(pagename, filename)
        write_file("wiki/%s/res/%s" % (pagename, filename),
                   form.data['image_data'])

    return edit_page(pagename, form)


def get_new_resource_name(pagename, suggested_name):
    filename = os.path.basename(suggested_name)
    if filename.endswith(".png") or filename.endswith(".PNG"):
        filename = filename[:-4]
    allowed = string.ascii_letters + string.digits
    filename = "".join([x for x in filename if x in allowed])
    if not is_valid_page_name(filename):  # TODO: Just reject it?
        filename = "image"
    existingNames = os.listdir("wiki/%s/res/" % pagename)
    if filename in existingNames:
        count = 1
        while True:
            proposedName = "%s%d" % (filename, count)
            if proposedName not in existingNames:
                break
            count += 1
        filename = proposedName
    return filename


# --- Static file serving ---
# "In production" we should let Apache do this instead.

static_content_files = [
    ('/nifki-lib.jar', 'nifki-lib.jar'),
    ('/tutorial.txt', 'templates/tutorial.txt'),
    ('/stylesheet.css', 'stylesheet.css'),
    ('/favicon.ico', 'favicon.ico'),
]
for filename in os.listdir('images'):
    static_content_files.append(
        ('/images/%s' % filename, 'images/%s' % filename))


def content_type_for(filename):
    # This is used in preference to mimetypes.guess_type(),
    # which is unlikely to do a good job.
    ext = filename.split('.')[-1]
    lookup = {
        'css': 'text/css',
        'txt': 'text/plain',
        'ico': 'image/vnd.microsoft.icon',
        'jar': 'application/java-archive',
        'png': 'image/png',
    }
    content_type = lookup[ext]
    # This isn't right, but it's good enough.
    if content_type.startswith("text/"):
        content_type += '; charset="utf-8"'
    return content_type


def make_static_file_endpoint(filename):
    contents = read_file(filename)
    endpoint = '_static_file_%d' % make_static_file_endpoint.i
    content_type = content_type_for(filename)
    globals_ = {'Response': flask.Response}
    locals_ = locals().copy()
    exec(
        'def %s(response=Response(contents, content_type=%r)):\n'
        '    return response\n'
        'func = %s' % (endpoint, content_type, endpoint),
        globals_, locals_)
    make_static_file_endpoint.i += 1
    app.logger.debug('Made static file handler for ' + filename)
    return locals_['func']

make_static_file_endpoint.i = 0

for url_path, filename in static_content_files:
    app.route(url_path, methods=['GET'])(make_static_file_endpoint(filename))

# --- End of Static file serving ---


app.debug = (os.getenv('DEBUG') == '1')
if __name__ == "__main__":
    # Always log to stderr (not needed for mod_wsgi-express)
    app.logger.addHandler(logging.StreamHandler())
    app.logger.setLevel(logging.DEBUG)
    app.run()
else:
    application = app
