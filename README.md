# Nextcloud S3 Storage Cleanup

## Kubernetes CronJob

Edit the `kubernetes-secret.yml` file and add the correct environment variables
for database and bucket names.

Then, apply them to the cluster:

    $ kubectl apply --validate -f kubernetes-secret.yml
    $ kubectl apply --validate -f kubernetes-cronjob.yml
