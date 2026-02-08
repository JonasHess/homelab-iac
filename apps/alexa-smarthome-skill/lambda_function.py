# -*- coding: utf-8 -*-

import os
import json
import logging
import urllib3

_debug = os.environ.get('DEBUG', '').lower() in ('1', 'y', 'yes', 'true', 'on')
_logger = logging.getLogger('HomeAssistant-SmartHome')
_logger.setLevel(logging.DEBUG if _debug else logging.INFO)


def lambda_handler(event, context):
    """Handle incoming Alexa Smart Home directive."""
    _logger.debug('Event: %s', event)

    base_url = os.environ.get('BASE_URL')
    assert base_url is not None, 'Please set BASE_URL environment variable'

    directive = event.get('directive', {})
    scope = directive.get('endpoint', {}).get('scope', directive.get('payload', {}).get('scope', {}))
    token = scope.get('token')

    if token is None and _debug:
        token = os.environ.get('LONG_LIVED_ACCESS_TOKEN')

    assert token, 'Could not get access token'

    verify_ssl = os.environ.get('NOT_VERIFY_SSL', '').lower() not in ('1', 'y', 'yes', 'true', 'on')

    http = urllib3.PoolManager(
        cert_reqs='CERT_REQUIRED' if verify_ssl else 'CERT_NONE',
        timeout=urllib3.Timeout(connect=2.0, read=10.0)
    )

    response = http.request(
        'POST',
        '{}/api/alexa/smart_home'.format(base_url),
        headers={
            'Authorization': 'Bearer {}'.format(token),
            'Content-Type': 'application/json',
        },
        body=json.dumps(event).encode('utf-8'),
    )

    if _debug:
        _logger.debug('Response status: %s', response.status)
        _logger.debug('Response body: %s', response.data.decode('utf-8'))

    if response.status >= 400:
        return {
            'event': {
                'payload': {
                    'type': 'INVALID_AUTHORIZATION_CREDENTIAL' if response.status in (401, 403) else 'INTERNAL_ERROR',
                    'message': response.data.decode("utf-8"),
                }
            }
        }

    return json.loads(response.data.decode('utf-8'))
