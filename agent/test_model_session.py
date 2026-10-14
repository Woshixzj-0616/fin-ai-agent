"""Browser model keys stay inside their own server-side sessions."""
from __future__ import annotations

import json
import os
import threading
import unittest
import urllib.request
from http.cookiejar import CookieJar
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from webui_http import Handler


class ModelSessionHttpTests(unittest.TestCase):
    def test_public_browser_sessions_do_not_share_keys(self):
        with patch.dict(os.environ, {"FIN_PUBLIC_MODE": "1", "LLM_API_KEY": "environment-example-key"}):
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                url = f"http://127.0.0.1:{server.server_port}/api/settings/model"
                first = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
                second = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))

                def get(opener):
                    with opener.open(url, timeout=5) as response:
                        return json.load(response)

                self.assertFalse(get(first)["configured"])
                self.assertFalse(get(second)["configured"])
                body = json.dumps({"api_key": "first-browser-example-key"}).encode()
                request = urllib.request.Request(
                    url, data=body, headers={"Content-Type": "application/json"}, method="POST"
                )
                with first.open(request, timeout=5) as response:
                    self.assertTrue(json.load(response)["configured"])
                self.assertTrue(get(first)["configured"])
                self.assertFalse(get(second)["configured"])
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
