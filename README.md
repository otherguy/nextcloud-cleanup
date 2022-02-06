# Nextcloud S3 Storage Cleanup

Nextcloud has several open issues ([#30762](https://github.com/nextcloud/server/issues/30762),
[#29841](https://github.com/nextcloud/server/issues/29841)) regarding its inability to clean up
left over chunks from bad or canceled uploads, when using S3 object storage as the primary
backend.

The [script itself](clean.php) is extremely simple and will clean up all chunks that are older
than a certain time limit.

As I'm running Nextcloud on [Scaleway Kubernetes](https://www.scaleway.com/en/kubernetes-kapsule/)
with Scaleway's S3 compatible [Object Storage](https://www.scaleway.com/en/object-storage/), this
script is optimized for my usecase but I will happily accept pull requests to make it more versatile
and work with other S3 compatible object storage providers.

## Prerequisites

The script was written for my own usecase and therefore currently only works with the following:

* MySQL database (or MariaDB)
* Nextcloud version 15 or higher
* Docker, Kubernetes or local PHP 8+

## Usage

To make the script as simple as possible, it's bundled in a [Docker image](Dockerfile) that can
be run either standalone or in Kubernetes.

This is designed to be run as a cronjob, e.g. every 60 minutes.

Simply copy the [`.env.example`](.env.example) file to a new `.env` file and adjust the values
to your setup. Make sure the location that you are running the script from can access both the
S3 bucket and the Nextcloud database (Firewall rules, IP blocking, etc.).

Then run:

```bash
$ docker run --rm --env-file=.env otherguy/nextcloud-cleanup:latest
Found 0 left over files.
Recovered 0 B from S3 storage.
```

## Kubernetes CronJob

Edit the [`kubernetes-secret.yml`](kubernetes-secret.yml) file and add the correct environment
variables for your database credentials and your S3 bucket details.

Then, apply the secret and [the CronJob](kubernetes-cronjob.yml) to the cluster:

```bash
$ kubectl apply --validate -f kubernetes-secret.yml -f kubernetes-cronjob.yml
secret/nextcloud-cleanup-config configured
cronjob.batch/nextcloud-cleanup configured
```
