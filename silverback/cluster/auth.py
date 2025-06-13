# NOTE: Most of this code borrowed from `fief-python`
# https://github.com/fief-dev/fief-python/blob/main/fief_client/client.py
import contextlib
import functools
import http
import http.server
import json
import pathlib
import queue
import typing
import urllib.parse
import uuid
import webbrowser
from collections.abc import Mapping
from enum import Enum
from string import Template
from typing import Any, Optional, TypedDict, Union
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx
from jwcrypto import jwk, jwt  # type: ignore[import-untyped]
from yaspin import yaspin
from yaspin.spinners import Spinners

from .utils import get_code_challenge, get_code_verifier, is_valid_hash

HTTPXClient = Union[httpx.Client, httpx.AsyncClient]


class ACR(str, Enum):
    LEVEL_ZERO = "0"
    """Level 0. No authentication was performed, a previous session was used."""
    LEVEL_ONE = "1"
    """Level 1. Password authentication was performed."""

    def __lt__(self, other: object) -> bool:
        return self._compare(other, True, True)

    def __le__(self, other: object) -> bool:
        return self._compare(other, False, True)

    def __gt__(self, other: object) -> bool:
        return self._compare(other, True, False)

    def __ge__(self, other: object) -> bool:
        return self._compare(other, False, False)

    def _compare(self, other: object, strict: bool, asc: bool) -> bool:
        if not isinstance(other, ACR):
            return NotImplemented  # pragma: no cover

        if self == other:
            return not strict

        for elem in ACR:
            if self == elem:
                return asc
            elif other == elem:
                return not asc
        raise RuntimeError()  # pragma: no cover


class TokenResponse(TypedDict):
    access_token: str
    id_token: str
    token_type: str
    expires_in: int
    refresh_token: Optional[str]


class AccessTokenInfo(TypedDict):
    id: uuid.UUID
    scope: list[str]
    acr: ACR
    permissions: list[str]
    access_token: str


class UserInfo(TypedDict):
    sub: str
    email: str
    tenant_id: str
    fields: dict[str, Any]


class ClientError(Exception):
    """Base  client error."""


class RequestError(ClientError):
    """The request to  server resulted in an error."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        self.message = f"[{status_code}] - {detail}"
        super().__init__(self.message)


class AccessTokenInvalid(ClientError):
    """The access token is invalid."""


class AccessTokenExpired(ClientError):
    """The access token is expired."""


class AccessTokenMissingScope(ClientError):
    """The access token is missing a required scope."""


class AccessTokenACRTooLow(ClientError):
    """The access token doesn't meet the minimum ACR level."""


class AccessTokenMissingPermission(ClientError):
    """The access token is missing a required permission."""


class IdTokenInvalid(ClientError):
    """The ID token is invalid."""


class AuthClient:
    base_url: str
    client_id: str

    _openid_configuration = None
    _jwks = None

    def __init__(
        self,
        base_url: str,
        client_id: str,
        *,
        host: Optional[str] = None,
    ) -> None:
        self.base_url = base_url
        self.client_id = client_id
        self.host = host

    def _get_endpoint_url(
        self,
        openid_configuration: dict[str, Any],
        field: str,
        *,
        absolute: bool = False,
    ) -> str:
        if not absolute:
            (scheme, netloc, *components) = urlsplit(self.base_url)
            host = self.host if self.host is not None else netloc
            host_base_url = urlunsplit((scheme, host, *components))
            return openid_configuration[field].split(host_base_url)[1]
        return openid_configuration[field]

    def _auth_url(
        self,
        openid_configuration: dict[str, Any],
        redirect_uri: str,
        *,
        state: Optional[str] = None,
        scope: Optional[list[str]] = None,
        code_challenge: Optional[str] = None,
        code_challenge_method: Optional[str] = None,
        lang: Optional[str] = None,
        extras_params: Optional[Mapping[str, str]] = None,
    ) -> str:
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
        }

        if state is not None:
            params["state"] = state

        if scope is not None:
            params["scope"] = " ".join(scope)

        if code_challenge is not None and code_challenge_method is not None:
            params["code_challenge"] = code_challenge
            params["code_challenge_method"] = code_challenge_method

        if lang is not None:
            params["lang"] = lang

        if extras_params is not None:
            params = {**params, **extras_params}

        authorization_endpoint = self._get_endpoint_url(
            openid_configuration, "authorization_endpoint", absolute=True
        )
        return f"{authorization_endpoint}?{urlencode(params)}"

    def _validate_access_token(
        self,
        access_token: str,
        jwks: jwk.JWKSet,
        *,
        required_scope: Optional[list[str]] = None,
        required_acr: Optional[ACR] = None,
        required_permissions: Optional[list[str]] = None,
    ) -> AccessTokenInfo:
        try:
            decoded_token = jwt.JWT(jwt=access_token, algs=["RS256"], key=jwks)
            claims = json.loads(decoded_token.claims)
            access_token_scope = claims.get("scp") or claims["scope"].split()
            if required_scope is not None:
                for scope in required_scope:
                    if scope not in access_token_scope:
                        raise AccessTokenMissingScope()

            try:
                acr = ACR(claims.get("acr", "0"))
            except ValueError as e:
                raise AccessTokenInvalid() from e

            if required_acr is not None:
                if acr < required_acr:
                    raise AccessTokenACRTooLow()

            permissions: list[str] = claims.get("permissions", [])
            if required_permissions is not None:
                for required_permission in required_permissions:
                    if required_permission not in permissions:
                        raise AccessTokenMissingPermission()

            return {
                "id": uuid.UUID(claims["sub"]),
                "scope": access_token_scope,
                "acr": acr,
                "permissions": permissions,
                "access_token": access_token,
            }

        except jwt.JWTExpired as e:
            raise AccessTokenExpired() from e
        except (jwt.JWException, KeyError, ValueError) as e:
            raise AccessTokenInvalid() from e

    def _decode_id_token(
        self,
        id_token: str,
        jwks: jwk.JWKSet,
        *,
        code: Optional[str] = None,
        access_token: Optional[str] = None,
    ) -> UserInfo:
        try:
            signed_id_token = jwt.JWT(jwt=id_token, algs=["RS256"], key=jwks)
            claims = json.loads(signed_id_token.claims)

            if "c_hash" in claims:
                if code is None or not is_valid_hash(code, claims["c_hash"]):
                    raise IdTokenInvalid()

            if "at_hash" in claims:
                if access_token is None or not is_valid_hash(access_token, claims["at_hash"]):
                    raise IdTokenInvalid()

        except (jwt.JWException, TypeError) as e:
            raise IdTokenInvalid() from e
        else:
            return claims

    def _get_openid_configuration_request(self, client: HTTPXClient) -> httpx.Request:
        return client.build_request("GET", "/.well-known/openid-configuration")

    def _get_auth_exchange_token_request(
        self,
        client: HTTPXClient,
        *,
        endpoint: str,
        code: str,
        redirect_uri: str,
        code_verifier: Optional[str] = None,
    ) -> httpx.Request:
        data = {
            "client_id": self.client_id,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        }
        if code_verifier is not None:
            data["code_verifier"] = code_verifier
        return client.build_request("POST", endpoint, data=data)

    def _get_auth_refresh_token_request(
        self,
        client: HTTPXClient,
        *,
        endpoint: str,
        refresh_token: str,
        scope: Optional[list[str]] = None,
    ) -> httpx.Request:
        data = {
            "client_id": self.client_id,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }
        if scope is not None:
            data["scope"] = " ".join(scope)

        return client.build_request("POST", endpoint, data=data)

    def _get_userinfo_request(
        self, client: HTTPXClient, *, endpoint: str, access_token: str
    ) -> httpx.Request:
        return client.build_request(
            "GET", endpoint, headers={"Authorization": f"Bearer {access_token}"}
        )

    def _get_update_profile_request(
        self,
        client: HTTPXClient,
        *,
        endpoint: str,
        access_token: str,
        data: dict[str, Any],
    ) -> httpx.Request:
        return client.build_request(
            "PATCH",
            endpoint,
            headers={"Authorization": f"Bearer {access_token}"},
            json=data,
        )

    def _get_change_password_request(
        self,
        client: HTTPXClient,
        *,
        endpoint: str,
        access_token: str,
        new_password: str,
    ) -> httpx.Request:
        return client.build_request(
            "PATCH",
            endpoint,
            headers={"Authorization": f"Bearer {access_token}"},
            json={"password": new_password},
        )

    def _get_email_change_request(
        self,
        client: HTTPXClient,
        *,
        endpoint: str,
        access_token: str,
        email: str,
    ) -> httpx.Request:
        return client.build_request(
            "PATCH",
            endpoint,
            headers={"Authorization": f"Bearer {access_token}"},
            json={"email": email},
        )

    def _get_email_verify_request(
        self,
        client: HTTPXClient,
        *,
        endpoint: str,
        access_token: str,
        code: str,
    ) -> httpx.Request:
        return client.build_request(
            "POST",
            endpoint,
            headers={"Authorization": f"Bearer {access_token}"},
            json={"code": code},
        )

    def _handle_request_error(self, response: httpx.Response):
        if response.is_error:
            raise RequestError(response.status_code, response.text)

    def auth_url(
        self,
        redirect_uri: str,
        *,
        state: Optional[str] = None,
        scope: Optional[list[str]] = None,
        code_challenge: Optional[str] = None,
        code_challenge_method: Optional[str] = None,
        lang: Optional[str] = None,
        extras_params: Optional[Mapping[str, str]] = None,
    ) -> str:
        openid_configuration = self._get_openid_configuration()
        return self._auth_url(
            openid_configuration,
            redirect_uri,
            state=state,
            scope=scope,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
            lang=lang,
            extras_params=extras_params,
        )

    def auth_callback(
        self, code: str, redirect_uri: str, *, code_verifier: Optional[str] = None
    ) -> tuple[TokenResponse, UserInfo]:
        token_response = self._auth_exchange_token(code, redirect_uri, code_verifier=code_verifier)
        jwks = self._get_jwks()
        userinfo = self._decode_id_token(
            token_response["id_token"],
            jwks,
            code=code,
            access_token=token_response.get("access_token"),
        )
        return token_response, userinfo

    def auth_refresh_token(
        self, refresh_token: str, *, scope: Optional[list[str]] = None
    ) -> tuple[TokenResponse, UserInfo]:
        token_endpoint = self._get_endpoint_url(self._get_openid_configuration(), "token_endpoint")
        with self._get_httpx_client() as client:
            request = self._get_auth_refresh_token_request(
                client,
                endpoint=token_endpoint,
                refresh_token=refresh_token,
                scope=scope,
            )
            response = client.send(request)

            self._handle_request_error(response)

            token_response = response.json()
        jwks = self._get_jwks()
        userinfo = self._decode_id_token(
            token_response["id_token"],
            jwks,
            access_token=token_response.get("access_token"),
        )
        return token_response, userinfo

    def validate_access_token(
        self,
        access_token: str,
        *,
        required_scope: Optional[list[str]] = None,
        required_acr: Optional[ACR] = None,
        required_permissions: Optional[list[str]] = None,
    ) -> AccessTokenInfo:
        jwks = self._get_jwks()
        return self._validate_access_token(
            access_token,
            jwks,
            required_scope=required_scope,
            required_acr=required_acr,
            required_permissions=required_permissions,
        )

    def userinfo(self, access_token: str) -> UserInfo:
        userinfo_endpoint = self._get_endpoint_url(
            self._get_openid_configuration(), "userinfo_endpoint"
        )
        with self._get_httpx_client() as client:
            request = self._get_userinfo_request(
                client, endpoint=userinfo_endpoint, access_token=access_token
            )
            response = client.send(request)

            self._handle_request_error(response)

            return response.json()

    @contextlib.contextmanager
    def _get_httpx_client(self):
        headers = {}
        if self.host is not None:
            headers["Host"] = self.host

        with httpx.Client(base_url=self.base_url, headers=headers, verify=True) as client:
            yield client

    def _get_openid_configuration(self) -> dict[str, Any]:
        if self._openid_configuration is not None:
            return self._openid_configuration

        with self._get_httpx_client() as client:
            request = self._get_openid_configuration_request(client)
            response = client.send(request)
            json = response.json()
            self._openid_configuration = json
            return json

    def _get_jwks(self) -> jwk.JWKSet:
        if self._jwks is not None:
            return self._jwks

        jwks_uri = self._get_endpoint_url(self._get_openid_configuration(), "jwks_uri")
        with self._get_httpx_client() as client:
            response = client.get(jwks_uri)
            self._jwks = jwk.JWKSet.from_json(response.text)
            return self._jwks

    def _auth_exchange_token(
        self, code: str, redirect_uri: str, *, code_verifier: Optional[str] = None
    ) -> TokenResponse:
        token_endpoint = self._get_endpoint_url(self._get_openid_configuration(), "token_endpoint")
        with self._get_httpx_client() as client:
            request = self._get_auth_exchange_token_request(
                client,
                endpoint=token_endpoint,
                code=code,
                redirect_uri=redirect_uri,
                code_verifier=code_verifier,
            )
            response = client.send(request)

            self._handle_request_error(response)

            return response.json()


class AuthError(Exception):
    """Base exception for auth"""


class AuthorizationCodeMissingError(AuthError):
    pass


class RefreshTokenMissingError(AuthError):
    pass


class NotAuthenticatedError(AuthError):
    pass


class CallbackHTTPServer(http.server.ThreadingHTTPServer):
    pass


class CallbackHTTPRequestHandler(http.server.BaseHTTPRequestHandler):
    # The following was borrowed from `oauth2-cli-auth`:
    # https://github.com/timo-reymann/python-oauth2-cli-auth/blob/main/oauth2_cli_auth/http_server.py
    SUCCESS_SVG = """
<svg xmlns="http://www.w3.org/2000/svg" width="154px" height="154px">
  <g fill="none" stroke="#22AE73" stroke-width="2">
    <circle cx="77" cy="77" r="72" style="stroke-dasharray:480px, 480px; stroke-dashoffset: 960px;">
    </circle>
    <circle
      id="colored"
      fill="#22AE73"
      cx="77"
      cy="77"
      r="72"
      style="stroke-dasharray:480px, 480px; stroke-dashoffset: 960px;"
    >
    </circle>
    <polyline
      class="st0"
      stroke="#fff"
      stroke-width="10"
      points="43.5,77.8 63.7,97.9 112.2,49.4 "
      style="stroke-dasharray:100px, 100px; stroke-dashoffset: 200px;"
    />
  </g>
</svg>"""
    """SVG to display checkmark"""

    ERROR_SVG = """
<svg xmlns="http://www.w3.org/2000/svg" width="154px" height="154px">
  <g fill="none" stroke="#F44812" stroke-width="2">
    <circle cx="77" cy="77" r="72" style="stroke-dasharray:480px, 480px; stroke-dashoffset: 960px;">
    </circle>
    <circle
      id="colored"
      fill="#F44812"
      cx="77"
      cy="77"
      r="72"
      style="stroke-dasharray:480px, 480px; stroke-dashoffset: 960px;"
    >
    </circle>
    <polyline
      class="st0"
      stroke="#fff"
      stroke-width="10"
      points="43.5,77.8 112.2,77.8 "
      style="stroke-dasharray:100px, 100px; stroke-dashoffset: 200px;"
    />
  </g>
</svg>"""
    """SVG to display error icon"""

    PAGE_TEMPLATE = Template(
        """
<html lang="$lang">
<head>
  <title>$title</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <meta name="charset" content="utf-8">
  <style>
    * {
      margin: 0;
      padding: 0;
    }

    body {
      font-family:
        -apple-system,
        BlinkMacSystemFont,
        "Segoe UI",
        Roboto,
        Helvetica,
        Arial,
        sans-serif,
        "Apple Color Emoji",
        "Segoe UI Emoji",
        "Segoe UI Symbol";
    }

    @media (prefers-color-scheme: dark) {
      body {
        background: rgb(34, 39, 46);
        color: rgb(173, 186, 199);
      }
    }

    html, body {
      display: flex;
      align-items: center;
      justify-content: center;
      height: 100%;
    }

    h1 {
      font-size: 4rem;
    }

    p {
      font-size: 1.4rem;
      max-width: 70ch;
    }

    .message {
      text-align: center;
    }

    .animation-ctn {
      text-align: center;
    }

    @keyframes checkmark {
      0% { stroke-dashoffset: 100px }
      100% { stroke-dashoffset: 0px }
    }

    @keyframes checkmark-circle {
      0% { stroke-dashoffset: 480px }
      100% { stroke-dashoffset: 960px }
    }

    @keyframes colored-circle {
      0% { opacity: 0 }
      100% { opacity: 100 }
    }

    .icon svg {
      padding: 1rem;
    }

    .icon svg polyline {
      -webkit-animation: checkmark 0.25s ease-in-out 0.7s backwards;
      animation: checkmark 0.25s ease-in-out 0.7s backwards
    }

    .icon svg circle {
      -webkit-animation: checkmark-circle 0.6s ease-in-out backwards;
      animation: checkmark-circle 0.6s ease-in-out backwards;
    }

    .icon svg circle#colored {
      -webkit-animation: colored-circle 0.6s ease-in-out 0.7s backwards;
      animation: colored-circle 0.6s ease-in-out 0.7s backwards;
    }
  </style>
  </head>

  <body>
    <div class="message">
      <div class="animation-ctn">
        <div class="icon">
          $svg
        </div>
      </div>

      <h1>$title</h1>
      <p>$message</p>
    </div>
  </body>
  <script>
    window.addEventListener("DOMContentLoaded", () => {{
      setTimeout(() => {{
        window.close();
      }}, 5000);
    }});
  </script>
</html>
    """
    )
    """Template for callback HTML page"""

    # NOTE: Rest of this code borrowed from `fief-python[cli]`
    # https://github.com/fief-dev/fief-python/blob/main/fief_client/integrations/cli.py
    def __init__(
        self,
        *args,
        queue: "queue.Queue[str]",
        **kwargs,
    ) -> None:
        self.queue = queue
        super().__init__(*args, **kwargs)

    def log_message(self, format: str, *args: typing.Any) -> None:
        pass

    def render_success_page(self, lang="en") -> str:
        return self.PAGE_TEMPLATE.substitute(
            lang=lang,
            title="Authentication Successful",
            message="Done! You can go back to your terminal! This page will auto-close in 5 secs.",
            svg=self.SUCCESS_SVG,
        )

    def render_error_page(self, query_params: dict[str, typing.Any], lang="en") -> str:
        return self.PAGE_TEMPLATE.substitute(
            lang=lang,
            title="Authentication Failed",
            message="""Something went wrong trying to authenticate your. Please try again.
    Error detail: {json.dumps(query_params)}""",
            svg=self.ERROR_SVG,
        )

    def do_GET(self):
        parsed_url = urllib.parse.urlparse(self.path)
        query_params = urllib.parse.parse_qs(parsed_url.query)

        try:
            code = query_params["code"][0]

        except (KeyError, IndexError):
            output = self.render_error_page(query_params).encode("utf-8")
            self.send_response(http.HTTPStatus.BAD_REQUEST)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(output)))
            self.end_headers()
            self.wfile.write(output)

        else:
            self.queue.put(code)

            output = self.render_success_page().encode("utf-8")
            self.send_response(http.HTTPStatus.OK)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(output)))
            self.end_headers()
            self.wfile.write(output)

        self.server.shutdown()


class Auth:
    _userinfo = None
    _tokens = None

    def __init__(self, client, credentials_path) -> None:
        self.client = client
        self.credentials_path = pathlib.Path(credentials_path)
        self._load_stored_credentials()

    def access_token_info(self, refresh=True):
        if self._tokens is None:
            raise NotAuthenticatedError()

        access_token = self._tokens["access_token"]
        try:
            return self.client.validate_access_token(access_token)
        except AccessTokenExpired:
            if refresh:
                self._refresh_access_token()
                return self.access_token_info()
            raise

    def current_user(self, refresh=False):
        if self._tokens is None or self._userinfo is None:
            raise NotAuthenticatedError()

        if refresh:
            access_token_info = self.access_token_info()
            userinfo = self.client.userinfo(access_token_info["access_token"])
            self._save_credentials(self._tokens, userinfo)
        return self._userinfo

    def authorize(
        self,
        server_address=("localhost", 51562),
        redirect_path="/callback",
        *,
        scope=None,
        lang=None,
        extras_params=None,
    ):
        redirect_uri = f"http://{server_address[0]}:{server_address[1]}{redirect_path}"

        scope_set: set[str] = set(scope) if scope else set()
        scope_set.add("openid")
        scope_set.add("offline_access")

        code_verifier = get_code_verifier()
        code_challenge = get_code_challenge(code_verifier)

        authorization_url = self.client.auth_url(
            redirect_uri,
            scope=list(scope_set),
            code_challenge=code_challenge,
            code_challenge_method="S256",
            lang=lang,
            extras_params=extras_params,
        )
        webbrowser.open(authorization_url)

        with yaspin(
            text="Please complete authentication in your browser.",
            spinner=Spinners.dots,
        ) as spinner:
            code_queue: queue.Queue[str] = queue.Queue()
            server = CallbackHTTPServer(
                server_address,
                functools.partial(CallbackHTTPRequestHandler, queue=code_queue),
            )

            server.serve_forever()

            try:
                code = code_queue.get(block=False)
            except queue.Empty as e:
                raise AuthorizationCodeMissingError() from e

            spinner.text = "Getting a token..."

            tokens, userinfo = self.client.auth_callback(
                code, redirect_uri, code_verifier=code_verifier
            )
            self._save_credentials(tokens, userinfo)

            spinner.ok("Successfully authenticated")

        return tokens, userinfo

    def _refresh_access_token(self):
        refresh_token = self._tokens.get("refresh_token")
        if refresh_token is None:
            raise RefreshTokenMissingError()
        tokens, userinfo = self.client.auth_refresh_token(refresh_token)
        self._save_credentials(tokens, userinfo)

    def _load_stored_credentials(self):
        if self.credentials_path.exists():
            with open(self.credentials_path) as file:
                try:
                    data = json.loads(file.read())
                    self._userinfo = data["userinfo"]
                    self._tokens = data["tokens"]
                except json.decoder.JSONDecodeError:
                    pass

    def _save_credentials(self, tokens, userinfo):
        self._tokens = tokens
        self._userinfo = userinfo
        with open(self.credentials_path, "w") as file:
            data = {"userinfo": userinfo, "tokens": tokens}
            file.write(json.dumps(data))
