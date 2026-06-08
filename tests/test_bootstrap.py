import json
from unittest.mock import MagicMock, patch

import harness.bootstrap as bootstrap


class _FakeWS:
    """Scripts the websocket handshake: auth_required -> auth_ok -> token result."""
    def __init__(self):
        self._outbox = [
            json.dumps({"type": "auth_required"}),
            json.dumps({"type": "auth_ok"}),
            json.dumps({"id": 1, "type": "result", "success": True, "result": "LLAT-XYZ"}),
        ]
        self.sent = []

    def __enter__(self): return self
    def __exit__(self, *a): return False
    def recv(self): return self._outbox.pop(0)
    def send(self, msg): self.sent.append(json.loads(msg))


def test_bootstrap_returns_llat():
    post_user = MagicMock(json=lambda: {"auth_code": "CODE123"}, text="{}")
    post_user.raise_for_status = lambda: None
    post_token = MagicMock(json=lambda: {"access_token": "ACCESS123"}, text="{}")
    post_token.raise_for_status = lambda: None
    fake_ws = _FakeWS()

    with patch("harness.bootstrap.requests.post", side_effect=[post_user, post_token]) as post, \
         patch("harness.bootstrap.connect", return_value=fake_ws):
        llat = bootstrap.bootstrap("http://pi-a:8123", "admin", "pw")

    assert llat == "LLAT-XYZ"
    # owner creation hit the onboarding endpoint with client_id
    assert post.call_args_list[0].args[0] == "http://pi-a:8123/api/onboarding/users"
    assert post.call_args_list[0].kwargs["json"]["client_id"] == "http://pi-a:8123/"
    # token exchange used the returned auth_code
    assert post.call_args_list[1].args[0] == "http://pi-a:8123/auth/token"
    assert post.call_args_list[1].kwargs["data"]["code"] == "CODE123"
    # ws: first send is auth with the access token, second mints the LLAT
    assert fake_ws.sent[0] == {"type": "auth", "access_token": "ACCESS123"}
    assert fake_ws.sent[1]["type"] == "auth/long_lived_access_token"
