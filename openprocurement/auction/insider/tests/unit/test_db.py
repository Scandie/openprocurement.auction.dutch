import errno
import datetime
import pytest

from couchdb.http import HTTPError

from openprocurement.auction.insider.tests.data.data import tender_data


def test_get_auction_info(auction, logger, mocker):

    with pytest.raises(AttributeError):
        assert auction.startDate

    auction.get_auction_info(prepare=False)

    assert isinstance(auction.startDate, datetime.datetime)

    auction.debug = False
    mock_get_tender_data = mocker.MagicMock()
    mock_get_tender_data.return_value = {
        'data': {
            'updated_from_get_tender_data': True,
            'auctionPeriod': {
                'startDate': '2017-12-12'
            }
        }
    }
    mocker.patch('openprocurement.auction.insider.mixins.get_tender_data', mock_get_tender_data)
    auction.generate_request_id()
    auction.startDate = None

    auction.get_auction_info()

    assert isinstance(auction.startDate, datetime.datetime)
    assert auction._auction_data['data']['updated_from_get_tender_data']
    mock_get_tender_data.assert_called_once_with(
        auction.tender_url + '/auction',
        user=auction.worker_defaults["resource_api_token"],
        request_id=auction.request_id,
        session=auction.session
    )

    mock_get_tender_data.side_effect = [{
        'data': {
            'updated_from_get_tender_data': True,
            'auctionPeriod': {
                'startDate': '2017-12-12'
            }
        }
    }, None]
    auction.auction_document = None
    mock_sys_exit = mocker.MagicMock()
    mocker.patch('openprocurement.auction.insider.mixins.sys.exit', mock_sys_exit)
    mock_end_auction_event = mocker.patch.object(auction, '_end_auction_event', autospec=True)
    mock_get_auction_document = mocker.patch.object(auction, 'get_auction_document', autospec=True)

    auction.get_auction_info(prepare=True)

    assert mock_get_tender_data.call_count == 3
    assert mock_get_tender_data.call_args_list[-2] == (
        (auction.tender_url, ),
        {
            'request_id': auction.request_id,
            'session': auction.session
        }
    )
    assert mock_get_tender_data.call_args_list[-1] == (
        (auction.tender_url + '/auction',),
        {
            'user': auction.worker_defaults["resource_api_token"],
            'request_id': auction.request_id,
            'session': auction.session
        }
    )
    assert mock_end_auction_event.set.call_count == 1
    assert mock_sys_exit.call_count == 1
    assert mock_get_auction_document.call_count == 1
    log_strings = logger.log_capture_string.getvalue().split('\n')
    assert log_strings[-2] == 'Auction UA-11111 not exists'

    mock_get_tender_data.side_effect = [{
        'data': {
            'updated_from_get_tender_data': True,
            'auctionPeriod': {
                'startDate': '2017-12-12'
            }
        }
    }, None]
    mock_save_document = mocker.patch.object(auction, 'save_auction_document', autospec=True)
    auction.auction_document = {1: 1}

    auction.get_auction_info(prepare=True)

    assert mock_get_tender_data.call_count == 5
    assert mock_get_tender_data.call_args_list[-2] == (
        (auction.tender_url,),
        {
            'request_id': auction.request_id,
            'session': auction.session
        }
    )
    assert mock_get_tender_data.call_args_list[-1] == (
        (auction.tender_url + '/auction',),
        {
            'user': auction.worker_defaults["resource_api_token"],
            'request_id': auction.request_id,
            'session': auction.session
        }
    )
    assert mock_save_document.call_count == 1
    assert mock_get_auction_document.call_count == 2
    assert auction.auction_document['current_stage'] == -100
    log_strings = logger.log_capture_string.getvalue().split('\n')
    assert log_strings[-2] == 'Cancel auction: UA-11111'


def test_prepare_public_document(auction):

    with pytest.raises(AttributeError):
        auction.prepare_public_document()

    auction.auction_document = {}

    result = auction.prepare_public_document()
    assert result == {}

    auction.auction_document = {'test_key': 'test_value'}
    result = auction.prepare_public_document()
    assert result is not auction.auction_document
    assert result == auction.auction_document


def test_prepare_auction_document(auction, mocker):

    mock_generate_request_id = mocker.patch.object(auction, 'generate_request_id', autospec=True)
    mock_get_auction_document = mocker.patch.object(auction, 'get_auction_document', autospec=True)
    mock_get_auction_document.return_value = True
    mock_save_auction_document = mocker.patch.object(auction, 'save_auction_document', autospec=True)
    mock_get_auction_info = mocker.patch.object(auction, 'get_auction_info', autospec=True)
    mock_get_auction_document.return_value = {'_rev': 'test_rev'}
    auction.startDate = datetime.datetime(2017, 12, 12)

    # sandbox_mode == True
    auction.prepare_auction_document()

    assert auction.auction_document['_rev'] == 'test_rev'
    assert auction.auction_document['mode'] == 'test'
    assert auction.auction_document['test_auction_data'] == tender_data
    assert auction.auction_document['test_auction_data'] is not tender_data
    assert mock_generate_request_id.call_count == 1
    assert mock_get_auction_document.call_count == 1
    assert mock_get_auction_document.call_count == 1
    assert mock_save_auction_document.call_count == 1
    assert mock_get_auction_info.call_count == 1
    assert len(auction.auction_document['stages']) == 16

    auction.worker_defaults['sandbox_mode'] = False

    auction.prepare_auction_document()

    assert auction.auction_document['_rev'] == 'test_rev'
    assert auction.auction_document['mode'] == 'test'
    assert auction.auction_document['test_auction_data'] == tender_data
    assert auction.auction_document['test_auction_data'] is not tender_data
    assert mock_generate_request_id.call_count == 2
    assert mock_get_auction_document.call_count == 2
    assert mock_get_auction_document.call_count == 2
    assert mock_save_auction_document.call_count == 2
    assert mock_get_auction_info.call_count == 2
    assert len(auction.auction_document['stages']) == 87


@pytest.mark.parametrize(
    'auction_data, auction_document, log_msg',
    [
        (
            'tender_data', 'document_from_auction_data', 'Prepare insider auction id=auction_id'
        ),
        (
            False, {'_rev': 'revision'}, 'Auction auction_id not exists'
        ),

    ],
    ids=['with auction data', 'without auction data']
)
def test_prepare_auction(auction, logger, mocker, auction_data, auction_document, log_msg):

    mock_generate_request_id = mocker.patch.object(auction, 'generate_request_id', autospec=True)
    mock_save_auction_document = mocker.patch.object(auction, 'save_auction_document', autospec=True)
    mock_get_auction_document = mocker.patch.object(auction, 'get_auction_document', autospec=True)
    mock_get_auction_document.return_value = {'_rev': 'revision'}

    mock_get_tender_data = mocker.MagicMock(return_value=auction_data)
    mocker.patch('openprocurement.auction.insider.mixins.get_tender_data', mock_get_tender_data)
    mock_prepare_auction_data = mocker.MagicMock(return_value=auction_document)
    mocker.patch('openprocurement.auction.insider.utils.prepare_auction_data', mock_prepare_auction_data)

    auction.tender_url = 'tender_url'
    auction.request_id = 'request_id'
    auction.session = 'session'
    auction.auction_doc_id = 'auction_id'

    auction.prepare_auction()
    log_strings = logger.log_capture_string.getvalue().split('\n')

    assert mock_generate_request_id.call_count == 1
    mock_get_tender_data.assert_called_once_with('tender_url', request_id='request_id', session='session')
    assert mock_get_auction_document.call_count == 1
    if auction_data:
        mock_prepare_auction_data.assert_called_once_with(auction_data)
        assert mock_save_auction_document.call_count == 1
    assert auction.auction_document == auction_document

    assert log_strings[-2] == log_msg


def test_save_auction_document(auction, db, mocker, logger):
    auction.prepare_auction_document()
    response = auction.save_auction_document()
    assert len(response) == 2
    assert response[0] == auction.auction_document['_id']
    assert response[1] == auction.auction_document['_rev']

    mock_db_save = mocker.patch.object(auction.db, 'save', autospec=True)
    for side, msg in zip([
        HTTPError('status code is >= 400'),
        Exception('unhandled error message'),
        Exception(errno.EPIPE, 'retryable error message'),
        ], [
            'Error while save document: status code is >= 400',
            'Unhandled error: unhandled error message',
            "Error while save document: (32, 'retryable error message')",
            ]):
        mock_db_save.side_effect = side
        auction.save_auction_document()
        log_strings = logger.log_capture_string.getvalue().split('\n')
        assert msg in log_strings


def test_get_auction_document(auction, db, mocker, logger):
    auction.prepare_auction_document()
    pub_doc = auction.db.get(auction.auction_doc_id)
    del auction.auction_document
    res = auction.get_auction_document()
    assert res == pub_doc

    log_strings = logger.log_capture_string.getvalue().split('\n')
    assert 'Rev error' not in log_strings
    auction.auction_document['_rev'] = 'wrong_rev'
    res = auction.get_auction_document()
    log_strings = logger.log_capture_string.getvalue().split('\n')
    assert res == pub_doc
    assert 'Rev error' in log_strings

    mock_db_get = mocker.patch.object(auction.db, 'get', autospec=True)
    for side, msg in zip([
                HTTPError('status code is >= 400'),
                Exception('unhandled error message'),
                Exception(errno.EPIPE, 'retryable error message'),
                res
            ],
            [
                'Error while get document: status code is >= 400',
                'Unhandled error: unhandled error message',
                "Error while get document: (32, 'retryable error message')",
                'Get auction document {0} with rev {1}'.format(res['_id'], res['_rev'])
            ]):
        mock_db_get.side_effect = side
        auction.get_auction_document()
        log_strings = logger.log_capture_string.getvalue().split('\n')
        assert msg in log_strings