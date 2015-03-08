import sys
sys.path.insert(0, '../')

from datetime import datetime
import re, smtplib, os, time, json, glob
from confidential import EMAIL_PASSWORD
from wtforms import Form, TextField, validators, TextAreaField
from flask import Flask, jsonify, request, flash, url_for, redirect, render_template, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from email.MIMEText import MIMEText
from email.mime.multipart import MIMEMultipart
from PIL import Image, ImageFile
from gevent.event import AsyncResult, Timeout
from gevent.queue import Empty, Queue
from shutil import rmtree
from hashlib import sha1
from stat import S_ISREG, ST_CTIME, ST_MODE

broadcast_queue = Queue()
app = Flask(__name__)
app.config.from_pyfile('app.cfg')
app.config['DEBUG'] = True
db = SQLAlchemy(app)
DATA_DIR = 'static/uploads'
KEEP_ALIVE_DELAY = 25
MAX_IMAGE_SIZE = 800, 600
MAX_IMAGES = 100
MAX_DURATION = 300

class Events(db.Model):
  __tablename__ = 'events'
  id = db.Column('id', db.Integer, primary_key=True)
  title = db.Column(db.String(100))
  author = db.Column(db.String(100))
  description = db.Column(db.String(200))
  pub_date = db.Column(db.DateTime)

  def __init__(self, title, author, description):
      self.title = title
      self.author = author
      self.description = description
      self.pub_date = datetime.now()

class Submit(Form):
    name = TextField()
    email = TextField()
    message = TextAreaField()

def is_email_address_valid(author):
  """Validate email address using regular expression."""
  if not re.match("^[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)*$", author):
      return False
  return True

def goGo(name, message, email):
    whole = "%s <br><br><br><i>This message was from: <b>%s</b>. Their e-mail address is <b>%s</b>.</i>" % (message, name, email)

    msg = MIMEMultipart('alternative')
    msg['Subject'] = "New message from %s!" % name

    part1 = MIMEText(whole, 'html')
    msg.attach(part1)

    o = smtplib.SMTP("smtp.gmail.com:587")
    o.starttls()
    o.login("web@aoiths.org", EMAIL_PASSWORD)
    o.sendmail(email, "web@aoiths.org", msg.as_string())
    o.close()

@app.route('/', methods=('GET', 'POST'))
def landing():
    form = Submit(request.form)
    if request.method == 'POST':
        goGo(form.name.data, form.message.data, form.email.data)
        flash('Your message has been sent.')
    return render_template('index.html', form=form)


@app.route('/english')
def english():
  return render_template('english.html')

@app.route('/math')
def math():
  return render_template('math.html')

@app.route('/science')
def science():
  return render_template('science.html')

@app.route('/cte')
def cte():
  return render_template('cte.html')

@app.route('/lang')
def forlang():
  return render_template('foreign.html')

@app.route('/social')
def social():
  return render_template('social.html')

@app.route('/events')
def show_all():
  return render_template('show_all.html', events=Events.query.order_by(Events.pub_date.desc()).all()  )

@app.route('/new', methods=['GET', 'POST'])
def new():
    if request.method == 'POST':
        if not request.form['title'] or not request.form['author'] or not request.form['description']:
            flash('Please enter all the fields', 'error')

        elif is_email_address_valid(request.form['author']):
            flash('Please enter a valid email address', 'error')

        else:
            event = Events(request.form['title'],
                               request.form['author'],
                               request.form['description'])

            db.session.add(event)
            db.session.commit()

            flash('Event was successfully submitted')

            return redirect(url_for('show_all'))

    return render_template('new.html')

@app.route('/data')
def names():
    data = {
        "first_names": ["John", "Jacob", "Julie", "Jennifer"],
        "last_names": ["Connor", "Johnson", "Cloud", "Ray"]
    }
    return jsonify(data)

def broadcast(message):
    """Notify all waiting waiting gthreads of message."""
    waiting = []
    try:
        while True:
            waiting.append(broadcast_queue.get(block=False))
    except Empty:
        pass
    print('Broadcasting {0} messages'.format(len(waiting)))
    for item in waiting:
        item.set(message)


def receive():
    """Generator that yields a message at least every KEEP_ALIVE_DELAY seconds.
    yields messages sent by `broadcast`.
    """
    now = time.time()
    end = now + MAX_DURATION
    tmp = None
    # Heroku doesn't notify when client disconnect so we have to impose a
    # maximum connection duration.
    while now < end:
        if not tmp:
            tmp = AsyncResult()
            broadcast_queue.put(tmp)
        try:
            yield tmp.get(timeout=KEEP_ALIVE_DELAY)
            tmp = None
        except Timeout:
            yield ''
        now = time.time()


def safe_addr(ip_addr):
    """Strip of the trailing two octets of the IP address."""
    return '.'.join(ip_addr.split('.')[:2] + ['xxx', 'xxx'])


def save_normalized_image(path, data):
    image_parser = ImageFile.Parser()
    try:
        image_parser.feed(data)
        image = image_parser.close()
    except IOError:
        raise
        return False
    image.thumbnail(MAX_IMAGE_SIZE, Image.ANTIALIAS)
    if image.mode != 'RGB':
        image = image.convert('RGB')
    image.save(path)
    return True


def event_stream(client):
    force_disconnect = False
    try:
        for message in receive():
            yield 'data: {0}\n\n'.format(message)
        print('{0} force closing stream'.format(client))
        force_disconnect = True
    finally:
        if not force_disconnect:
            print('{0} disconnected from stream'.format(client))


@app.route('/post', methods=['POST'])
def post():
    sha1sum = sha1(request.data).hexdigest()
    target = os.path.join(DATA_DIR, '{0}.jpg'.format(sha1sum))
    message = json.dumps({'src': target,
                          'ip_addr': safe_addr(request.access_route[0])})
    try:
        if save_normalized_image(target, request.data):
            broadcast(message)  # Notify subscribers of completion
    except Exception as e:  # Output errors
        return '{0}'.format(e)
    return 'success'


@app.route('/stream')
def stream():
    return Response(event_stream(request.access_route[0]),
                          mimetype='text/event-stream')


@app.route('/add_photo')
def home():
    image_infos = []
    for filename in os.listdir(DATA_DIR):
        filepath = os.path.join(DATA_DIR, filename)
        file_stat = os.stat(filepath)
        if S_ISREG(file_stat[ST_MODE]):
            image_infos.append((file_stat[ST_CTIME], filepath))
    global images
    images = []
    for i, (_, path) in enumerate(sorted(image_infos, reverse=True)):
        if i >= MAX_IMAGES:
            os.unlink(path)
            continue
        images.append('<div class="row uniform"><div class="4u 6u(medium) 12u$(xsmall)"><img alt="User uploaded image" src="{0}" /></div><div class="4u 6u(medium) 12u$(xsmall)"><input type="submit" value="SHIT"></div></div>'
                      .format(path))
    return render_template('add_photo.html') % (MAX_IMAGES, '\n'.join(images))

@app.route('/uploads/<filename>')
def send_file(filename):
    return send_from_directory(DATA_DIR, filename)

@app.route('/gallery')
def gallery():
    images = glob.glob("./static/uploads/*.jpg")
    return render_template('gallery.html', images=images)

@app.route('/delete_photos/<filename>')
def download_file(filename):
    file_path = DATA_DIR
    file_handle = open(file_path, 'r')
    @after_this_request
    def remove_file(response):
        os.remove(file_path)
        return response
    return send_file(file_handle)

if __name__ == '__main__':
  app.run(debug=True)
