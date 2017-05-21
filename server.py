#!/usr/bin/python

import sys, os, cgi, time, textwrap, string, shutil
import cherrypy

#from cherrypy.lib.filter.decodingfilter import DecodingFilter
#from cherrypy.lib.filter.encodingfilter import EncodingFilter
#from cherrypy.lib.filter.gzipfilter import GzipFilter

cherrypy.debug = False

os.chdir("/home/apt1002/python/nifki")

def readfile(filename):
    """
    Returns the contents of the specified file as a (byte) string.
    This subroutine closes the file after reading it, which is necessary to
    prevent running out of file descriptors before the garbage collector
    collects the file objects.
    """
    f = file(filename, "rb")
    contents = f.read()
    f.close()
    return contents

def template(filename, **kwargs):
    """
    A simple templating mechanism to keep big bits of HTML out of the code.
    """
    html = readfile(os.path.join("templates", filename+".html")).decode("UTF-8")
    return html % kwargs

def template2(filename, title, **kwargs):
    """
    A more complicated templating mechanism. Loads 'filename' and substitutes
    'kwargs' into it, then subtitutes the result and 'title' into the file
    'base-template.html'.
    """
    return template("base-template",
        title=title, body=template(filename, **kwargs))

def group(items, groupSize, pad):
    """
    Appends copies of 'pad' to 'items' until its length is a multiple of
    'groupSize'. Then groups the items 'groupSize' at a time and returns a list
    of groups.
    """
    while len(items)%groupSize != 0: items.append(pad)
    g = iter(items)
    return [
        [g.next() for x in range(groupSize)]
        for y in range(len(items)/groupSize)]

def httpError(code, message):
    """
    Returns an error page containing 'message' with HTTP response code 'code'.
    """
    cherrypy.response.headerMap['Status'] = code
    return template("error", message=message)

def parseProperties(properties):
    """
    Parses a file of the form of "properties.txt" and returns its contents as a
    dict. This is used, for example, to retrieve the width and height of a
    game for inclusion in the applet tag.
    """
    ans = dict(name="", width="256", height="256", msPerFrame="40",
               debug="false")
    for line in properties.split("\n"):
        hash = line.find("#")
        if hash>=0: line = line[:hash]
        line = line.strip()
        if line:
            colon = line.find(":")
            if colon==-1: raise ValueError("Colon missing from '"+line+"'")
            ans[line[:colon]] = line[colon+1:].strip()
    return ans

def makeProperties(properties):
    """Takes a dict and returns a file of the form of "properties.txt"."""
    return "".join(["%s: %s\n" % (k, v) for (k, v) in properties])

def allCharsIn(s, allowed):
    for c in s:
        if c not in allowed: return False
    return True

def allUpperCase(s): return allCharsIn(s, string.ascii_uppercase)
def allAlphaNum(s): return allCharsIn(s, string.ascii_letters + string.digits)

def isValidPageName(pagename):
    """
    Page names must start with a letter, must contain only letters and
    digits, must not be entirely capital letters, and must have at least three
    characters and at most twenty.
    """
    if not (3 <= len(pagename) <= 20): return False
    if pagename[0] not in string.ascii_letters: return False
    if not allAlphaNum(pagename): return False
    if allUpperCase(pagename): return False
    return True

class Wiki:
    """Handles the root URL of the wiki."""
    # Now expressed as settings, see end of file.
    #_cpFilterList = [
    #    DecodingFilter('utf-8'),
    #    EncodingFilter('utf-8'),
    #    GzipFilter()]

    @cherrypy.expose
    def index(self):
        return template("welcome-to-nifki")

class Pages:
    """
    Handles everything in the /pages/ URL-space. Most things are accessed as
    /pages/<pagename>/<action>/, which CherryPy will pass to the 'default()'
    method.
    """
    @cherrypy.expose
    def index(self):
        pagenames = [page for page in os.listdir("wiki") if page != "nifki-out"]
        pagenames.sort()
        pagenames = ['   <li><a href="/pages/%s/play/">%s</a></li>'
                     % (page, page)
                     for page in pagenames]
        return template2("list-of-all-pages",
            title="List of All Pages", pagenames="\n".join(pagenames))

    @cherrypy.expose
    def default(self, pagename, action=None, *path, **params):
        if not isValidPageName(pagename):
            return httpError(404, "Bad page name '%s'" % cgi.escape(pagename))
        if action==None:
            raise cherrypy.HTTPRedirect("/pages/%s/play/" % pagename)
        if action.endswith(".jar"): return self.jar(pagename)
        if action=="play": return self.play(pagename)
        if action=="edit": return self.edit(pagename)
        if action=="save": return self.save(pagename, **params)
        if action=="res": return self.res(pagename, path[0])
        return httpError(404, "Unknown action: %s" % action)
    
    def jar(self, pagename):
        """
        Returns the jar file for this page. Note that the requested filename is
        completely ignored. In fact, we vary the filename in order to defeat the
        browser cache.
        """
        jarfile = file(os.path.join("wiki/nifki-out", pagename+".jar"), "rb")
        jar = jarfile.read()
        jarfile.close()
        cherrypy.response.headerMap["Content-Type"] = "application/java-archive"
        # Mozilla refuses to cache anything without a "Last-Modified" header,
        # and ludicrously downloads a copy of the jar file for every entry
        # contained within it. Really! Top quality!
        cherrypy.response.headerMap["Last-Modified"] = (
          cherrypy.response.headerMap["Date"] )
        return jar
    
    def play(self, pagename):
        """
        Returns the page with the applet tag on it, if the game compiled
        successfully, otherwise returns a page showing the compiler output.
        """
        if os.path.exists("wiki/nifki-out/%s.jar" % pagename):
            propsfile = file("wiki/%s/properties.txt" % pagename, "rb")
            props = parseProperties(propsfile.read().decode("UTF-8"))
            propsfile.close()
            return template("playing",
                pagename=pagename,
                width=int(props["width"]), height=int(props["height"]),
                random=int(time.time()),
                name=props["name"])
        elif os.path.exists("wiki/nifki-out/%s.err" % pagename):
            errfile = file("wiki/nifki-out/%s.err" % pagename, "rb")
            err = errfile.read().decode("UTF-8")
            errfile.close()
            lines = []
            for line in err.split("\n"):
                for shortline in textwrap.wrap(line, width=80):
                    lines.append(shortline)
            err = "\n".join(lines)
            return template("compiler-output",
                pagename=pagename, err=cgi.escape(err))
        else:
            raise cherrypy.HTTPRedirect("/pages/%s/edit/" % pagename)

    def edit(self, pagename):
        if not os.path.isdir("wiki/%s/" % pagename):
            return template("no-such-page", pagename=pagename)
        # Load "source.sss" file.
        sourcefile = file("wiki/%s/source.sss" % pagename, "rb")
        source = sourcefile.read().decode("UTF-8")
        sourcefile.close()
        # Load "properties.txt" file.
        propsfile = file("wiki/%s/properties.txt" % pagename, "rb")
        props = parseProperties(propsfile.read().decode("UTF-8"))
        propsfile.close()
        # Return an editing page.
        return self.editPage(pagename, None, source, props["width"],
                             props["height"], props["msPerFrame"],
                             props["name"], props["debug"]!="false", pagename)
    
    def editPage(self, pagename, errormessage, source, width, height,
                 msPerFrame, name, showDebug, newpage):
        """
        Returns an edit page populated with the specified data. All fields are
        strings except 'showDebug' which is a boolean. 'errormessage' can be
        'None'. This method compiles the table of images itself.
        """
        # Wrap up 'errormessage' in an HTML paragraph.
        if errormessage:
            errormessage = ('<p class="error" align="center">%s</p>' %
                            cgi.escape(errormessage))
        else:
            errormessage = ""
        # Compile a list of the images attached to the page.
        imagelist = os.listdir("wiki/%s/res" % pagename)
        imagelist.sort()
        imagelist = [
            # "    <td>%s</td>" % image
            template("fragments/editing-image", pagename=pagename, image=image)
            for image in imagelist]
        imagelist = [
            "    <tr>\n" + "\n".join(row) + "\n    </tr>"
            for row in group(imagelist, 5, "     <td></td>")]
        if imagelist==[]:
            imagelist = "   <p align=\"center\">No pictures</p>"
        else:
            imagelist = (
                """   <table cols="5" rows="%d" align="center">\n"""
                    % len(imagelist) +
                "\n".join(imagelist) +
                "\n   </table>")
        return template("editing",
            pagename=pagename,
            errormessage=errormessage,
            source=cgi.escape(source),
            width=cgi.escape(width), height=cgi.escape(height),
            msPerFrame=cgi.escape(msPerFrame),
            name=cgi.escape(name),
            debugChecked=["", "checked"][showDebug],
            imagelist=imagelist,
            newpage=cgi.escape(newpage),
            uploadedImage="")

    def save(self, pagename, source, width, height, msPerFrame, name,
             newpage, uploadedImage=None, debug=None, save=None, upload=None):
        if upload:
            return self.uploadImage(pagename, source, width, height, msPerFrame,
                                    name, newpage, uploadedImage, debug)
        errormessage = None
        if newpage==pagename: pass  # Unchanged.
        elif not isValidPageName(newpage):
            errormessage = (
                "Your changes have not been saved because '%s' is "
                "not allowed as a page name. Page names must start with a "
                "letter, must contain only letters and digits, must not be "
                "entirely capital letters, and must have at least three "
                "characters and at most twenty.") % newpage
        elif os.path.isdir("wiki/%s/" % newpage):
            errormessage = (
                "Your changes have not been saved because a page called "
                "'%s' already exists.") % newpage
        else:
            # New page.
            shutil.copytree("wiki/%s/" % pagename, "wiki/%s/" % newpage)
        # Check that width, height and msPerFrame are integers.
        try: int(width), int(height), int(msPerFrame)
        except ValueError:
            errormessage = (
                "The width, height and frame rate must all be integers.")
        # Either save or return to the editing page with an error message.
        if errormessage:
            return self.editPage(pagename, errormessage, source, width, height,
                                 msPerFrame, name, debug!=None, newpage)
        else:
            return self.savePage(newpage, source, width, height,
                                 msPerFrame, name, debug!=None)

    def uploadImage(self, pagename, source, width, height, msPerFrame, name,
                    newpage, uploadedImage, debug):
        errormessage = None
        magic = uploadedImage.file.read(4)
        if not magic:
            errormessage = "Image file not found."
        elif magic[1:4] != "PNG" and magic[:2] != "\xFF\xD8":
            errormessage = "Images must be in PNG or JPEG format."
        else:
            imageData = magic + uploadedImage.file.read(102400-len(magic))
            if uploadedImage.file.read(1):
                errormessage = "Image files must be smaller than 100K."
        if not errormessage:
            fname = uploadedImage.filename
            fname = os.path.basename(fname)
            if (fname.lower().endswith(".png") or
                fname.lower().endswith(".jpg") or
                fname.lower().endswith(".jpeg")):
                fname = fname[:fname.rfind('.')]
            allowed = string.ascii_letters + string.digits
            fname = "".join([x for x in fname if x in allowed])
            if not isValidPageName(fname):
                fname = "image"
            existingNames = os.listdir("wiki/%s/res" % pagename)
            if fname in existingNames:
                count = 1
                while True:
                    proposedName = "%s%d" % (fname, count)
                    if proposedName not in existingNames: break
                    count += 1
                fname = proposedName
            imageFile = file("wiki/%s/res/%s" % (pagename, fname), "wb")
            imageFile.write(imageData)
            imageFile.close()
        return self.editPage(pagename, errormessage, source, width, height,
                             msPerFrame, name, debug!=None, newpage)

    def savePage(self, pagename, source, width, height, msPerFrame, name,
                 showDebug):
        """
        Saves changes to 'pagename'. Runs the compiler. Returns a redirect to
        the 'play' page. All parameters are strings except 'showDebug' which is
        a boolean.
        """
        # Save the source file, 'source.sss'.
        sourcefile = file("wiki/%s/source.sss" % pagename, "wb")
        sourcefile.write(source.encode("UTF-8"))
        sourcefile.close()
        # Save the properties file, 'properties.txt'.
        props = makeProperties([
            ("name", name),
            ("width", int(width)), ("height", int(height)),
            ("msPerFrame", int(msPerFrame)),
            ("debug", ["false", "true"][showDebug])
        ])
        propsfile = file("wiki/%s/properties.txt" % pagename, "wb")
        propsfile.write(props.encode("UTF-8"))
        propsfile.close()
        # Run the compiler.
        errcode = os.system("/usr/local/share/jdk1.5/bin/java -jar compiler.jar wiki %s" % pagename)
        if errcode:
            cherrypy.response.headerMap['Status'] = 500
            return template("compiler-error")
        else:
            raise cherrypy.HTTPRedirect("/pages/%s/play/" % pagename)
    
    def res(self, pagename, imagename):
        imagefile = file("wiki/%s/res/%s" % (pagename, imagename), "rb")
        image = imagefile.read()
        imagefile.close()
        cherrypy.response.headerMap["Content-Type"] = "image/png"
        return image

cherrypy.root = Wiki()
cherrypy.root.pages = Pages()
cherrypy.config.update(
  {
    'global': {
      # 'server.protocolVersion': "HTTP/1.1",
      #  ^^ Doesn't seem to work!
      #     Docs suggest CherryPy supports HTTP 1.0 only unless behind Apache.

      'logDebugInfoFilter.logBuildTime': False,
      'logDebugInfoFilter.logPageSize': False,
      'server.socketPort': 8080,
      'server.socketHost': '127.0.0.1',
      'server.logAccessFile': '/var/log/nifki',
      'server.logFile': '/var/log/nifki',
      'staticFilter.root': '/home/apt1002/python/nifki',
      'decodingFilter.on': True,
      'decodingFilter.encoding': 'utf-8',
      'encodingFilter.on': True,
      'encodingFilter.encoding': 'utf-8',
      'gzipFilter.on': True,
#      'tidyFilter.on': True,  # only turn on temporarily, breaks stuff.
      'tidyFilter.strictXml': True,
      'tidyFilter.tmpDir': '/tmp',
      'tidyFilter.tidyPath': 'tidy',
    },
    '/nifki-lib.jar' : {
      'staticFilter.on': True,
      'staticFilter.file': 'nifki-lib.jar'
     },
    '/stylesheet.css' : {
      'staticFilter.on': True,
      'staticFilter.file': 'stylesheet.css'
     },
    '/tutorial.txt' : {
      'staticFilter.on': True,
      'staticFilter.file': 'templates/tutorial.txt'
     },
    '/favicon.ico' : {
      'staticFilter.on': True,
      'staticFilter.file': 'favicon.ico'
     },
    '/images' : {
      'staticFilter.on': True,
      'staticFilter.dir': 'images'
     },
  })
##print cherrypy.config.configMap
cherrypy.server.start()
