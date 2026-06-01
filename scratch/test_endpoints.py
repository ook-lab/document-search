import urllib.request
import urllib.error

def test_url(url, expected_status=200):
    print(f"Testing {url}...")
    try:
        # We use a custom redirect handler to see where we get redirected
        class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                print(f"  Redirecting from {req.full_url} to {newurl} (status: {code})")
                return None
                
        opener = urllib.request.build_opener(NoRedirectHandler)
        req = urllib.request.Request(url)
        with opener.open(req) as response:
            status = response.getcode()
            print(f"  Status code: {status}")
            if status == expected_status:
                print("  [SUCCESS]")
            else:
                print(f"  [FAILED] Expected {expected_status}, got {status}")
    except urllib.error.HTTPError as e:
        print(f"  HTTPError: {e.code}")
        if e.code == expected_status:
            print("  [SUCCESS]")
        else:
            print(f"  [FAILED] Expected {expected_status}, got {e.code}")
    except Exception as e:
        print(f"  Error: {e}")

# 1. Test root / (should return 200, rendering editor.html)
test_url("http://127.0.0.1:5058/")

# 2. Test /editor (should return redirect, but since redirect is blocked in our handler it returns None/raises HTTPError)
test_url("http://127.0.0.1:5058/editor", expected_status=302)

# 3. Test /reader (should return 404 since it was removed)
test_url("http://127.0.0.1:5058/reader", expected_status=404)
