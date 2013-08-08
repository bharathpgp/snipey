import logging
from snipey import tasks
from snipey import db
from snipey.model import Event, Group, Snipe
import requests
import simplejson as json
from datetime import datetime
import config

EVENT_STREAM_URL = 'http://stream.meetup.com/2/open_events'


def rsvp_now():
    tasks.rsvp.delay(48598382, 133591952, '2b2c40beabee27c1e0641213d6aab32a')


def open_event_stream(url=EVENT_STREAM_URL, since_time=''):
    """ Open a stream to the Meetup Open Events API

    Documentation located at:
    http://www.meetup.com/meetup_api/docs/stream/2/open_events/

    An optional since_time can be passed in to back process events
    that occured after a certain time.

    """
    logging.info('open_event_stream. url:%s, since_time: %s'
                 % (url, since_time))

    return requests.get(url, stream=True)


def reconnect(since_time=datetime.now()):
    """ Reconnect to the stream API, retrieving data from the provided
    since_time. since_time defaults to now.

    """
    logging.info('reconnect. since_time: %s' % since_time)
    process_stream(open_event_stream(since_time=since_time))


def process_stream(request):
    """ Process the incoming request stream.

    If the connection is lost, proceed to reconnect, providing
    the mtime of lost connection.

    TODO: This whole unit of work should be done as a celery task.

    """
    logging.info('processing stream.')

    for line in request.iter_lines():
        data = json.loads(line)

        meetup_group_id = data['group']['id']
        event_url = data['event_url']

        # logging.info('meetup_group_id: %s, event_url: %s'
        #              % (meetup_group_id, event_url))

        parse_snipes(meetup_group_id, event_url)


def parse_snipes(meetup_group_id, event_url):
    """ If a matching subscription exists in the system for the meetup
    group_id, create the event and process snipes for all users.

    """

    group = Group.query.filter(Group.meetup_id == meetup_group_id).first()

    if group and group.subscribers:
        event_id = get_event_id(event_url)
        event = create_event(group, event_id)
        create_snipes(event)


def get_event_id(event_url):
    """ Parse the event_id from the event_url.

    The event_url is in the format:
    http://www.meetup.com/<group_name>/events/<event_id>/

    TODO: This is terrible. Take the time to re-implement this with
    a regular expression.

    """

    return event_url.split('/')[-2]


def create_event(group, event_id):
    """ Make a call to the Meetup API to retrieve event information.

    Use the data to create a reference event in the database.

    NOTE: Since the event is being retrieved without authorization
    information, private groups are not supported.

    TODO: Implement Error handing, especially if the event is not found.
    """

    params = {
        'fields': 'rsvp_rules',
        'key': config.MEETUP_API_KEY,
    }

    url = "%sevent/%s" % (config.BASE_URL, event_id)
    resp = requests.get(url=url, params=params)
    data = resp.json()

    name = data['name']
    open_time = data['rsvp_rules'].get('open_time')
    if open_time:
        open_time = datetime.utcfromtimestamp(open_time//1000).replace(
            microsecond=open_time % 1000*1000)

    event = Event(group=group,
                  meetup_id=event_id,
                  name=name,
                  rsvp_open_time=open_time)

    db.session.add(event)
    db.session.commit()

    logging.info('created event with id: %s' % event.id)

    return event


def create_snipes(event):
    """ Given a group id and an event, create snipes for all subscribers.

    Dispatch celery tasks for every snipe. If the event has an
    rsvp_open time, dispatch the task with an eta.

    """
    logging.info('creating snipes')
    for user in event.group.subscribers:
        snipe = Snipe(event_id=event.id, user_id=user.id)
        logging.info('created snipe with id: %s' % snipe.id)
        # TODO dispatch celery task
        db.session.add(snipe)

    db.session.commit()