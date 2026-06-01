$gcloudPath = "C:\Users\ookub\AppData\Local\Google\Cloud SDK\google-cloud-sdk\bin"
if ($env:PATH -notlike "*$gcloudPath*") {
    $env:PATH += ";$gcloudPath"
}

gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=sansu-base" --limit=30 --project=consummate-yew-479020-u2 --order=desc --format="table(timestamp, textPayload)"
