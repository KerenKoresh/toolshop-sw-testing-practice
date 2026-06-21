"""Contract / property-based tests.

Schemathesis reads the app's own OpenAPI document and generates a wide range of
requests for every endpoint, asserting that responses conform to the schema and
that the server never 500s. This catches drift between the spec and the code.
"""
import schemathesis
from hypothesis import settings

import app as app_module

schema = schemathesis.openapi.from_wsgi(path="/api/openapi.json", app=app_module.app)


@schema.parametrize()
@settings(max_examples=50, deadline=None)
def test_api_conforms_to_openapi(case):
    case.call_and_validate()
