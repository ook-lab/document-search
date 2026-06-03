import urllib.request
import urllib.error

url = 'https://quiz-maker-ocdx2fd4ia-an.a.run.app/api/subjects'
print(f"Requesting {url} ...")
try:
    with urllib.request.urlopen(url) as response:
        print("Status Code:", response.status)
        print("Response Body:")
        print(response.read().decode('utf-8'))
except urllib.error.HTTPError as e:
    print("HTTP Error Code:", e.code)
    print("HTTP Error Response Body:")
    print(e.read().decode('utf-8'))
except Exception as e:
    print("An error occurred:", e)
