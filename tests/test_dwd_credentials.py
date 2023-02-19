import datetime
import json
import os
from unittest import mock

import pytest  # type: ignore
from google.auth import _helpers, crypt, exceptions, transport
from google.oauth2 import credentials, service_account
from six.moves import http_client

from google_auth_plugins import dwd_credentials
from google_auth_plugins.dwd_credentials import Credentials

DATA_DIR = os.path.join(os.path.dirname(__file__), "", "data")

with open(os.path.join(DATA_DIR, "privatekey.pem"), "rb") as fh:
    PRIVATE_KEY_BYTES = fh.read()

SERVICE_ACCOUNT_JSON_FILE = os.path.join(DATA_DIR, "service_account.json")

ID_TOKEN_DATA = (
    "eyJhbGciOiJSUzI1NiIsImtpZCI6ImRmMzc1ODkwOGI3OTIyOTNhZDk3N2Ew"
    "Yjk5MWQ5OGE3N2Y0ZWVlY2QiLCJ0eXAiOiJKV1QifQ.eyJhdWQiOiJodHRwc"
    "zovL2Zvby5iYXIiLCJhenAiOiIxMDIxMDE1NTA4MzQyMDA3MDg1NjgiLCJle"
    "HAiOjE1NjQ0NzUwNTEsImlhdCI6MTU2NDQ3MTQ1MSwiaXNzIjoiaHR0cHM6L"
    "y9hY2NvdW50cy5nb29nbGUuY29tIiwic3ViIjoiMTAyMTAxNTUwODM0MjAwN"
    "zA4NTY4In0.redacted"
)
ID_TOKEN_EXPIRY = 1564475051

with open(SERVICE_ACCOUNT_JSON_FILE, "rb") as fh:
    SERVICE_ACCOUNT_INFO = json.load(fh)

SIGNER = crypt.RSASigner.from_string(PRIVATE_KEY_BYTES, "1")
TOKEN_URI = "https://example.com/oauth2/token"

class RequestMockResponse:
    def __init__(self, json_data, status_code):
        self.data = json.dumps(json_data)
        self.status = status_code

    def __call__(self, **kwargs):
        return self
        
class MockResponse:
    def __init__(self, json_data, status_code):
        self.json_data = json_data
        self.status_code = status_code

    def json(self):
        return self.json_data

@pytest.fixture
def mock_donor_credentials():
    with mock.patch("google.oauth2._client.jwt_grant", autospec=True) as grant:
        grant.return_value = (
            "source token",
            _helpers.utcnow() + datetime.timedelta(seconds=500),
            {},
        )
        yield grant

@pytest.fixture
def mock_request_sign():
    with mock.patch(
        "google.auth.transport.requests.Request", autospec=True
    ) as auth_session:
        data = {"signedJwt": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"}
        auth_session.return_value = RequestMockResponse(data, http_client.OK)
        yield auth_session

@pytest.fixture
def mock_authorizedsession_sign():
    with mock.patch(
        "google.auth.transport.requests.AuthorizedSession.request", autospec=True
    ) as auth_session:
        data = {"keyId": "1", "signedBlob": "c2lnbmF0dXJl"}
        auth_session.return_value = MockResponse(data, http_client.OK)
        yield auth_session

class TestDwdCredentials(object):

    SERVICE_ACCOUNT_EMAIL = "service-account@example.com"
    TARGET_PRINCIPAL = "dwd@project.iam.gserviceaccount.com"
    TARGET_SCOPES = ["https://www.googleapis.com/auth/admin.directory.group.readonly"]
    SUBJECT = "john.doe@example.com"
    # DELEGATES: List[str] = []
    # Because Python 2.7:
    DELEGATES = []  # type: ignore
    SOURCE_CREDENTIALS = service_account.Credentials(
        SIGNER, SERVICE_ACCOUNT_EMAIL, TOKEN_URI
    )
    USER_SOURCE_CREDENTIALS = credentials.Credentials(token="ABCDE")
    IAM_SIGN_ENDPOINT_OVERRIDE = (
        "https://us-east1-iamcredentials.googleapis.com/v1/projects/-"
        + "/serviceAccounts/{}:signJwt".format(SERVICE_ACCOUNT_EMAIL)
    )

    def make_credentials(
        self,
        source_credentials=SOURCE_CREDENTIALS,
        subject=SUBJECT,
        target_principal=TARGET_PRINCIPAL,
        iam_sign_endpoint_override=None
    ):

        return Credentials(
            source_credentials=source_credentials,
            subject=subject,
            target_principal=target_principal,
            target_scopes=self.TARGET_SCOPES,
            delegates=self.DELEGATES,
            iam_sign_endpoint_override=iam_sign_endpoint_override,
        )

    def test_make_from_user_credentials(self):
        credentials = self.make_credentials(
            source_credentials=self.USER_SOURCE_CREDENTIALS
        )
        assert not credentials.valid
        assert credentials.expired

    def test_default_state(self):
        credentials = self.make_credentials()
        assert not credentials.valid
        assert credentials.expired

    def make_request(
        self,
        data,
        status=http_client.OK,
        headers=None,
        side_effect=None,
        use_data_bytes=True,
    ):
        response = mock.create_autospec(transport.Response, instance=False)
        response.status = status
        response.data = _helpers.to_bytes(data) if use_data_bytes else data
        response.headers = headers or {}

        request = mock.create_autospec(transport.Request, instance=False)
        request.side_effect = side_effect
        request.return_value = response

        return request

    @pytest.mark.parametrize("use_data_bytes", [True, False])
    def test_refresh_success(self, use_data_bytes, mock_donor_credentials, mock_request_sign):
        credentials = self.make_credentials()
        token = "token"
        expires_in = 500
        response_body = {"access_token": token, "expires_in": expires_in}

        request = self.make_request(
            data=json.dumps(response_body),
            status=http_client.OK,
            use_data_bytes=use_data_bytes,
        )

        credentials.refresh(request)

        assert credentials.valid
        assert not credentials.expired

    @pytest.mark.parametrize("use_data_bytes", [True, False])
    def test_refresh_success_iam_sign_endpoint_override(
        self, use_data_bytes, mock_donor_credentials,
    ):
        credentials = self.make_credentials(
            iam_sign_endpoint_override=self.IAM_SIGN_ENDPOINT_OVERRIDE
        )

        token = "token"
        expires_in = 500
        response_body = {"access_token": token, "expires_in": expires_in}

        with mock.patch(
            "google.auth.transport.requests.Request", autospec=True
        ) as auth_session:
            data = {"signedJwt": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"}
            mock_response = mock.Mock(return_value=RequestMockResponse(data, http_client.OK))
            auth_session.return_value = mock_response

            request = self.make_request(
                data=json.dumps(response_body),
                status=http_client.OK,
                use_data_bytes=use_data_bytes,
            )

            credentials.refresh(request)

            assert credentials.valid
            assert not credentials.expired

            # Confirm override endpoint used.
            mock_response_kwargs = mock_response.call_args[1]
            assert mock_response_kwargs["url"] == self.IAM_SIGN_ENDPOINT_OVERRIDE


    @pytest.mark.parametrize("time_skew", [100, -100])
    def test_refresh_source_credentials(self, time_skew, mock_request_sign):
        credentials = self.make_credentials()

        # Source credentials is refreshed only if it is expired within
        # _helpers.REFRESH_THRESHOLD from now. We add a time_skew to the expiry, so
        # source credentials is refreshed only if time_skew <= 0.
        credentials._source_credentials.expiry = (
            _helpers.utcnow()
            + _helpers.REFRESH_THRESHOLD
            + datetime.timedelta(seconds=time_skew)
        )
        credentials._source_credentials.token = "Token"

        with mock.patch(
            "google.oauth2.service_account.Credentials.refresh", autospec=True
        ) as source_cred_refresh:
            token = "token"
            expires_in = 500
            response_body = {"access_token": token, "expires_in": expires_in}
            request = self.make_request(
                data=json.dumps(response_body), status=http_client.OK
            )

            credentials.refresh(request)

            assert credentials.valid
            assert not credentials.expired

            # Source credentials is refreshed only if it is expired within
            # _helpers.REFRESH_THRESHOLD
            if time_skew > 0:
                source_cred_refresh.assert_not_called()
            else:
                source_cred_refresh.assert_called_once()

    def test_refresh_failure_malformed_expire_time(self, mock_donor_credentials, mock_request_sign):
        credentials = self.make_credentials()
        token = "token"

        expires_in = "500"
        response_body = {"access_token": token, "expires_in": expires_in}

        request = self.make_request(
            data=json.dumps(response_body), status=http_client.OK
        )

        with pytest.raises(exceptions.RefreshError) as excinfo:
            credentials.refresh(request)

        assert excinfo.match(dwd_credentials._DWD_ERROR)

        assert not credentials.valid
        assert credentials.expired

    def test_refresh_failure_unauthorzed(self, mock_donor_credentials, mock_request_sign):
        credentials = self.make_credentials()

        response_body = {
            "error": {
                "code": 403,
                "message": "The caller does not have permission",
                "status": "PERMISSION_DENIED",
            }
        }

        request = self.make_request(
            data=json.dumps(response_body), status=http_client.UNAUTHORIZED
        )

        with pytest.raises(exceptions.RefreshError) as excinfo:
            credentials.refresh(request)

        assert excinfo.match(dwd_credentials._DWD_ERROR)

        assert not credentials.valid
        assert credentials.expired

    def test_refresh_failure_http_error(self, mock_donor_credentials, mock_request_sign):
        credentials = self.make_credentials()

        response_body = {}

        request = self.make_request(
            data=json.dumps(response_body), status=http_client.HTTPException
        )

        with pytest.raises(exceptions.RefreshError) as excinfo:
            credentials.refresh(request)

        assert excinfo.match(dwd_credentials._DWD_ERROR)

        assert not credentials.valid
        assert credentials.expired

    def test_refresh_failure_unable_to_sign_impersonated_token(self, mock_donor_credentials):
        credentials = self.make_credentials()

        with mock.patch("google.auth.transport.requests.Request", autospec=True) as auth_session:
            data = {"error": {"code": 403, "message": "unauthorized"}}
            auth_session.return_value = RequestMockResponse(data, http_client.FORBIDDEN)

            with pytest.raises(exceptions.TransportError) as excinfo:
                credentials.refresh(self.make_request(data=json.dumps({})))
            assert excinfo.match(dwd_credentials._DWD_SIGN_ERROR)

    def test_expired(self):
        credentials = self.make_credentials()
        assert credentials.expired

    def test_signer(self):
        credentials = self.make_credentials()
        assert isinstance(credentials.signer, dwd_credentials.Credentials)

    def test_signer_email(self):
        credentials = self.make_credentials(target_principal=self.TARGET_PRINCIPAL)
        assert credentials.signer_email == self.TARGET_PRINCIPAL

    def test_service_account_email(self):
        credentials = self.make_credentials(target_principal=self.TARGET_PRINCIPAL)
        assert credentials.service_account_email == self.TARGET_PRINCIPAL

    @pytest.mark.parametrize("target_principal", [None, TARGET_PRINCIPAL])
    def test__target_prinpal(self, target_principal):
        credentials = self.make_credentials(target_principal=target_principal)
        if target_principal is None:
            assert credentials._target_principal == credentials._source_credentials.service_account_email
        else:
            assert credentials._target_principal == target_principal

    def test__target_prinpal_failure(self):
        with pytest.raises(ValueError):
            source_credentials = service_account.Credentials(
                SIGNER, None, TOKEN_URI
            )
            self.make_credentials(target_principal=None, source_credentials=source_credentials)

    def test_sign_bytes(self, mock_donor_credentials, mock_authorizedsession_sign, mock_request_sign):
        credentials = self.make_credentials()
        token = "token"
        expires_in = 500
        response_body = {"access_token": token, "expires_in": expires_in}

        response = mock.create_autospec(transport.Response, instance=False)
        response.status = http_client.OK
        response.data = _helpers.to_bytes(json.dumps(response_body))

        request = mock.create_autospec(transport.Request, instance=False)
        request.return_value = response

        credentials.refresh(request)

        assert credentials.valid
        assert not credentials.expired

        signature = credentials.sign_bytes(b"signed bytes")
        assert signature == b"signature"

    def test_sign_bytes_failure(self):
        credentials = self.make_credentials()

        with mock.patch(
            "google.auth.transport.requests.AuthorizedSession.request", autospec=True
        ) as auth_session:
            data = {"error": {"code": 403, "message": "unauthorized"}}
            auth_session.return_value = MockResponse(data, http_client.FORBIDDEN)

            with pytest.raises(exceptions.TransportError) as excinfo:
                credentials.sign_bytes(b"foo")
            assert excinfo.match("'code': 403")

    @pytest.mark.parametrize("use_data_bytes", [True, False])
    def test_with_quota_project(
        self, use_data_bytes, mock_donor_credentials, mock_request_sign
    ):
        credentials = self.make_credentials()
        # iam_endpoint_override should be copied to created credentials.
        quota_project_creds = credentials.with_quota_project("project-foo")

        token = "token"
        expires_in = 500
        response_body = {"access_token": token, "expires_in": expires_in}

        request = self.make_request(
            data=json.dumps(response_body),
            status=http_client.OK,
            use_data_bytes=use_data_bytes,
        )

        quota_project_creds.refresh(request)

        assert quota_project_creds._quota_project_id == "project-foo"
        assert quota_project_creds.valid
        assert not quota_project_creds.expired

    def test_with_scopes(self):
        credentials = self.make_credentials()
        credentials._target_scopes = []
        assert credentials.requires_scopes is True
        credentials = credentials.with_scopes(["fake_scope1", "fake_scope2"])
        assert credentials.requires_scopes is False
        assert credentials._target_scopes == ["fake_scope1", "fake_scope2"]

    def test_with_scopes_provide_default_scopes(self):
        credentials = self.make_credentials()
        credentials._target_scopes = []
        credentials = credentials.with_scopes(
            ["fake_scope1"], default_scopes=["fake_scope2"]
        )
        assert credentials._target_scopes == ["fake_scope1"]
