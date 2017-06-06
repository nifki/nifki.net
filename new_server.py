#!/usr/bin/python2.7

import collections
import datetime
import errno
import logging
import os
import pipes
import re
import shutil
import string
import subprocess
import sys
import textwrap
import time

import flask

from flask import Flask, redirect, url_for, request, render_template
from werkzeug.exceptions import NotFound, BadRequestKeyError
from werkzeug.routing import UnicodeConverter
from flask_wtf import FlaskForm
from flask_wtf.file import FileField
from wtforms import (
    StringField, TextAreaField, IntegerField, BooleanField, SubmitField)
from wtforms.validators import InputRequired, ValidationError


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


class SaveChangesForm(FlaskForm):
    name = StringField('name', validators=[])
    width = IntegerField('width', validators=[InputRequired()])
    height = IntegerField('height', validators=[InputRequired()])
    msPerFrame = IntegerField('msPerFrame', validators=[InputRequired()])
    debug = BooleanField('debug', validators=[])
    source = TextAreaField('source', validators=[InputRequired()])
    newpage = StringField('newpage', validators=[InputRequired()])
    upload = SubmitField('upload', validators=[])
    uploadedImage = FileField('uploadedImage', validators=[])

    def __init__(self, pagename, *args, **kwargs):
        super(SaveChangesForm, self).__init__(*args, **kwargs)
        self.pagename = pagename
        self.image_data = None

    def first_error(self):
        if self.width.errors or self.height.errors or self.msPerFrame.errors:
            return "The width, height and frame rate must all be integers."
        for field in iter(self):
            if field.errors:
                return next(iter(field.errors))

    def has_upload(self):
        return self.upload.data

    def validate_newpage(self, field):
        newpage = field.data
        if newpage == self.pagename:
            pass  # Unchanged.
        elif not is_valid_page_name(newpage):
            raise ValidationError(
                "Your changes have not been saved because '%s' is "
                "not allowed as a page name. Page names must start with a "
                "letter, must contain only letters and digits, must not be "
                "entirely capital letters, and must have at least three "
                "characters and at most twenty." % newpage)  # TODO: >20?
        elif os.path.isdir("wiki/%s/" % newpage):
            raise ValidationError(
                "Your changes have not been saved because a page called "
                "'%s' already exists." % newpage)

    def validate_uploadedImage(self, field):
        if not self.has_upload():
            return
        uploadedImage = request.files['uploadedImage']
        if not uploadedImage:
            raise ValidationError("Upload file not provided.")
        self.image_data = uploadedImage.read(102400)
        if len(self.image_data) < 4  or  self.image_data[1:4] != "PNG":
            raise ValidationError("Images must be in PNG format.")
        if len(self.image_data) >= 102400:
            raise ValidationError("Image files must be smaller than 100K.")


game_property_types = collections.OrderedDict([
    ('name', unicode),  # This is actually the tagline.
    ('width', int),
    ('height', int),
    ('msPerFrame', int),
    ('debug', bool),
])
class GameProperties(collections.namedtuple(
    '_GameProperties', ' '.join(game_property_types)
)):
    @staticmethod
    def load(filename):
        """
        Parses a file of the form of "properties.txt" and returns its contents
        as a GameProperties. This is used, for example, to retrieve the width
        and height of a game for inclusion in the applet tag.
        """
        result = dict(
            name="", width="256", height="256", msPerFrame="40", debug="false")
        for line in read_utf8(filename).split("\n"):
            line = line.strip()
            if line and not line.startswith('#'):
                colon = line.find(":")
                if colon == -1:
                    raise ValueError("Colon missing from '" + line + "'")
                key, value = line[:colon], line[colon + 1:]
                result[key] = value.strip()
        return GameProperties(**result)

    def save(self, filename):
        def encode(value):
            if isinstance(value, bool):
                return ['false', 'true'][value]
            else:
                return unicode(value)

        lines = []
        for key in game_property_types:
            value = getattr(self, key)
            lines.append("%s: %s\n" % (key, encode(value)))

        write_utf8(filename, ''.join(lines))

    def to_form(self, pagename, newpage, source):
        data = {}
        data['newpage'] = newpage
        data['source'] = source
        for key in game_property_types:
            value_str = getattr(self, key)
            key_type = game_property_types[key]
            if key_type is bool:
                data[key] = {'true': True, 'false': False}[value_str]
            elif key_type is int:
                data[key] = int(value_str)
            else:
                assert key_type is unicode
                data[key] = value_str
        return SaveChangesForm(pagename, formdata=None, data=data)

    @staticmethod
    def check_save_changes_form(pagename):
        form = SaveChangesForm(pagename)
        if form.validate_on_submit():
            props = GameProperties(
                name=form.name.data,
                width=form.width.data,
                height=form.height.data,
                msPerFrame=form.msPerFrame.data,
                debug=form.debug.data)
        else:
            props = None
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
    pagenames = [
        page for page in os.listdir("wiki") if is_valid_page_name(page)
    ]
    return render_template(
        "list-of-all-pages.html", pagenames=sorted(pagenames))


@app.route('/pages/<PageName:pagename>/')
def page_index(pagename):
    return redirect("/pages/%s/play/" % pagename)


@app.route('/pages/<PageName:pagename>/res/<PageName:filename>')
def page_resource(pagename, filename):
    data = read_file("wiki/%s/res/%s" % (pagename, filename))
    return flask.Response(data, 200, content_type="image/png")


@app.route('/pages/<PageName:pagename>/<_anything>.jar')
def jar(pagename, _anything):
    """
    Returns the jar file for this page. Note that the requested filename is
    completely ignored. In fact, we vary the filename in order to defeat the
    browser cache.
    """
    jarfile = read_file("wiki/nifki-out/%s.jar" % (pagename,))
    response = flask.Response(jarfile, 200, {
        "Content-Type": "application/java-archive"})
    # Mozilla refuses to cache anything without a "Last-Modified" header,
    # and ludicrously downloads a copy of the jar file for every entry
    # contained within it. Really! Top quality!
    now = datetime.datetime.now()
    response.headers["Last-Modified"] = response.headers["Date"] = now
    return response


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
    form = props.to_form(pagename, pagename, source)
    return edit_page(pagename, form)


def edit_page(pagename, form):
    """
    Returns an edit page populated with the specified data. All fields are
    strings except 'debug' which is a boolean. This method compiles the
    table of images itself.
    """
    return Utf8Response(render_template(
        "editing.html",
        pagename=pagename,
        error_message=form.first_error() or '',
        image_list=sorted(os.listdir("wiki/%s/res" % pagename)),
        form=form,
        uploadedImage=''))


@app.route('/pages/<PageName:pagename>/save/', methods=['POST'])
def save(pagename):
    form, props = GameProperties.check_save_changes_form(pagename)
    newpage = form.newpage.data
    source = form.source.data
    if props is None:
        return edit_page(pagename, form)
    if form.has_upload():
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
        assert not form.first_error()
        image_data = form.image_data
        filename = request.files['uploadedImage'].filename
        filename = get_new_resource_name(pagename, filename)
        write_file("wiki/%s/res/%s" % (pagename, filename), image_data)

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
