# -*- coding: utf-8 -*-
import datetime
import logging
import os
import pytest
import yaml
import couchdb
import json

from dateutil.tz import tzlocal
from flask import redirect
from mock import MagicMock
from pytz import timezone as tz
from StringIO import StringIO
from mock import patch

from openprocurement.auction.insider.auction import Auction, SCHEDULER
from openprocurement.auction.insider.server import app as server_app
from openprocurement.auction.insider.forms import BidsForm, form_handler
from openprocurement.auction.insider.mixins import LOGGER
from openprocurement.auction.insider.constants import (
    PRESEALEDBID, SEALEDBID, PREBESTBID
)
from openprocurement.auction.insider.tests.data.data import (
    tender_data, bidders
)


def update_auctionPeriod(data):
    new_start_time = (datetime.datetime.now(tzlocal()) +
                      datetime.timedelta(seconds=120)).isoformat()
    if 'lots' in data['data']:
        for lot in data['data']['lots']:
            lot['auctionPeriod']['startDate'] = new_start_time
    data['data']['auctionPeriod']['startDate'] = new_start_time


PWD = os.path.dirname(os.path.realpath(__file__))
worker_defaults_file_path = os.path.join(
    os.getcwd(),
    "openprocurement/auction/insider/tests/data/auction_worker_insider.yaml")
with open(worker_defaults_file_path) as stream:
    worker_defaults = yaml.load(stream)


@pytest.yield_fixture(scope="function")
def auction():
    update_auctionPeriod(tender_data)

    yield Auction(
        tender_id=tender_data['data']['tenderID'],
        worker_defaults=yaml.load(open(worker_defaults_file_path)),
        auction_data=tender_data
    )


@pytest.fixture(scope='function')
def db(request):
    server = couchdb.Server("http://" + worker_defaults['COUCH_DATABASE'].split('/')[2])
    name = worker_defaults['COUCH_DATABASE'].split('/')[3]

    def delete():
        del server[name]

    if name in server:
        delete()
    server.create(name)
    request.addfinalizer(delete)


class LogInterceptor(object):
    def __init__(self, logger):
        self.log_capture_string = StringIO()
        self.test_handler = logging.StreamHandler(self.log_capture_string)
        self.test_handler.setLevel(logging.INFO)
        logger.addHandler(self.test_handler)


@pytest.fixture(scope='function')
def logger():
    return LogInterceptor(LOGGER)


@pytest.fixture(scope='function')
def scheduler():
    return SCHEDULER


@pytest.fixture(scope='function')
def bids_form(auction, db):
    form = BidsForm()
    auction.prepare_auction_document()
    form.document = auction.auction_document
    return form


@pytest.yield_fixture(scope='function')
def app(db):
    update_auctionPeriod(tender_data)
    logger = MagicMock()
    logger.name = 'some-logger'
    app_auction = Auction(
        tender_id=tender_data['data']['tenderID'],
        worker_defaults=yaml.load(open(worker_defaults_file_path)),
        auction_data=tender_data
    )
    app_auction.prepare_auction_document()
    app_auction.schedule_auction()
    app_auction.start_auction()
    server_app.config.update(app_auction.worker_defaults)
    server_app.logger_name = logger.name
    server_app._logger = logger
    server_app.config['auction'] = app_auction
    server_app.config['timezone'] = tz('Europe/Kiev')
    server_app.config['SESSION_COOKIE_PATH'] = '/{}/{}'.format(
        'auctions', app_auction.auction_doc_id)
    server_app.config['SESSION_COOKIE_NAME'] = 'auction_session'
    server_app.oauth = MagicMock()
    server_app.bids_form = BidsForm
    server_app.form_handler = MagicMock()
    server_app.form_handler.return_value = {'data': 'ok'}
    server_app.remote_oauth = MagicMock()
    authorized_response = {
        u'access_token': u'aMALGpjnB1iyBwXJM6betfgT4usHqw',
        u'token_type': u'Bearer',
        u'expires_in': 86400,
        u'refresh_token': u'uoRKeSJl9UFjuMwOw6PikXuUVp7MjX',
        u'scope': u'email'
    }
    server_app.remote_oauth.authorized_response.side_effect = [
        None, authorized_response, authorized_response]
    server_app.remote_oauth.authorize.return_value = \
        redirect('https://my.test.url')
    for bidder in bidders.values():
        server_app.logins_cache[bidder['remote_oauth']] = {
            u'bidder_id': bidder['bidder_id'],
            u'expires':
                (datetime.datetime.now(tzlocal()) +
                 datetime.timedelta(0, 600)).isoformat()
        }
    server_app.auction_bidders = {
        u'f7c8cd1d56624477af8dc3aa9c4b3ea3': {
            'clients': {},
            'channels': {}
        }}

    yield server_app.test_client()


@pytest.yield_fixture(scope='function')
def auction_app(app):
    for bidder in bidders.values():
        if bidder['bidder_id'] == '2' * 32 or bidder['bidder_id'] == '1' * 32:
            continue
        app.application.config['auction'].bidders_data.append(
            {'id': bidder['bidder_id']})
        app.application.config['auction'].mapping[bidder['bidder_id']] = \
            len(app.application.config['auction'].mapping) + 1
    app.application.form_handler = form_handler
    yield app


@pytest.yield_fixture(scope='function')
def sealedbid_app(auction_app):
    headers = {'Content-Type': 'application/json'}
    session = {
        'remote_oauth': None,
        'client_id': 'b3a000cdd006b4176cc9fafb46be0273'
    }
    stage = \
        auction_app.application.config['auction'].auction_document['stages'][1]
    for i in xrange(0, 6):
        auction_app.application.config['auction'].next_stage(stage)
    data = {
        'bidder_id': bidders['dutch_bidder']['bidder_id'],
        'bid': 33250
    }
    session['remote_oauth'] = bidders['dutch_bidder']['remote_oauth']
    with patch('openprocurement.auction.insider.server.session', session), \
         patch('openprocurement.auction.insider.forms.session', session):
        auction_app.post('/postbid', data=json.dumps(data), headers=headers)
    yield auction_app


def pytest_addoption(parser):
    parser.addoption("--worker", action="store_true", help="runs worker test", dest='worker')


def pytest_configure(config):
    # register an additional marker
    config.addinivalue_line("markers", "worker: mark test to run only if worker option is passed (--worker)")


def pytest_runtest_setup(item):
    worker_marker = item.get_marker("worker")
    if worker_marker is not None:
        if not item.config.getoption("worker", False):
            pytest.skip("test requires worker option (--worker)")
