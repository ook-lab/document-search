$env:PATH = "C:\Users\ookub\AppData\Local\Google\Cloud SDK\google-cloud-sdk\bin;" + $env:PATH

$PROJECT_ID = "consummate-yew-479020-u2"
$SERVICE_NAME = "quiz-maker"

Write-Host "=== Latest Stderr Logs ==="
$filter = 'resource.type="cloud_run_revision" AND resource.labels.service_name="quiz-maker" AND logName="projects/consummate-yew-479020-u2/logs/run.googleapis.com%2Fstderr"'
gcloud logging read $filter --limit=10 --project $PROJECT_ID --format=json
