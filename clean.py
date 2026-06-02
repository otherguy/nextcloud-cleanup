import os
import math
import logging
import argparse
import boto3
import mysql.connector
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

def readable_bytes(bytes_count):
    if bytes_count == 0:
        return "0 B"
    units = ('B', 'KB', 'MB', 'GB', 'TB', 'PB', 'EB', 'ZB', 'YB')
    i = int(math.floor(math.log(bytes_count, 1024)))
    p = math.pow(1024, i)
    s = round(bytes_count / p, 2)
    return f"{s} {units[i]}"

def get_db_connection():
    return mysql.connector.connect(
        host=os.getenv('DATABASE_HOST'),
        user=os.getenv('DATABASE_USER'),
        password=os.getenv('DATABASE_PASSWORD'),
        database=os.getenv('DATABASE_NAME')
    )

def scan_s3_orphans(s3, s3_bucket, cursor_read, filename_pattern, dry_run):
    """
    Parcourt TOUS les objets S3 et supprime ceux dont le fileid
    n'existe plus dans oc_filecache.
    """
    logger.info("Mode SCAN-S3-ORPHANS : listing de tous les objets S3...")

    # Charger tous les fileids connus en DB dans un set (rapide à interroger)
    logger.info("Chargement des fileids depuis oc_filecache...")
    cursor_read.execute("SELECT fileid FROM oc_filecache")
    known_ids = {row['fileid'] for row in cursor_read.fetchall()}
    logger.info(f"  → {len(known_ids)} entrées DB chargées.")

    paginator = s3.get_paginator('list_objects_v2')
    pages = paginator.paginate(Bucket=s3_bucket)

    total_scanned = 0
    total_orphans = 0
    size_recovered = 0
    objects_to_delete = []

    for page in pages:
        for obj in page.get('Contents', []):
            key = obj['Key']
            total_scanned += 1

            # On ne traite que les objets au format urn:oid:<int>
            if not key.startswith('urn:oid:'):
                continue
            try:
                fileid = int(key.split('urn:oid:')[1])
            except ValueError:
                continue

            if fileid not in known_ids:
                size = obj.get('Size', 0)
                msg_prefix = "[DRY-RUN] " if dry_run else ""
                logger.info(f"  - {msg_prefix}Orphelin S3 : {key} ({readable_bytes(size)})")
                total_orphans += 1
                size_recovered += size

                if not dry_run:
                    # Suppression par batch de 1000 (limite API S3)
                    objects_to_delete.append({'Key': key})
                    if len(objects_to_delete) >= 1000:
                        s3.delete_objects(Bucket=s3_bucket, Delete={'Objects': objects_to_delete})
                        objects_to_delete = []

    # Vider le dernier batch
    if not dry_run and objects_to_delete:
        s3.delete_objects(Bucket=s3_bucket, Delete={'Objects': objects_to_delete})

    logger.info(f"Scan terminé. {total_scanned} objets S3 analysés.")
    logger.info(f"Orphelins trouvés : {total_orphans}. Espace récupérable : {readable_bytes(size_recovered)}.")


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(description='Nextcloud S3 Cleanup')
    parser.add_argument('--dry-run', action='store_true', help='Simuler les suppressions')
    parser.add_argument('--scan-all', action='store_true', help='Vérifier les fichiers DB manquants sur S3')
    parser.add_argument('--scan-s3-orphans', action='store_true', help='Supprimer les objets S3 sans entrée DB')
    args = parser.parse_args()

    deletion_grace_period = int(os.getenv('DELETION_GRACE_PERIOD', 86400))
    filename_pattern = os.getenv('NEXTCLOUD_FILENAME_PATTERN', 'urn:oid:%d')
    s3_bucket = os.getenv('AWS_BUCKET')

    s3 = boto3.client('s3',
        region_name=os.getenv('AWS_DEFAULT_REGION'),
        endpoint_url=os.getenv('AWS_ENDPOINT'),
        aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
        aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY')
    )

    db_read = get_db_connection()
    db_write = get_db_connection()
    cursor_read = db_read.cursor(dictionary=True, buffered=True)
    cursor_write = db_write.cursor()

    # ── Nouveau mode ──────────────────────────────────────────────────────────
    if args.scan_s3_orphans:
        scan_s3_orphans(s3, s3_bucket, cursor_read, filename_pattern, args.dry_run)
        cursor_read.close()
        db_read.close()
        return

    # ── Modes existants ───────────────────────────────────────────────────────
    if args.scan_all:
        logger.info("Mode SCAN-ALL : vérification de l'intégrité...")
        query = "SELECT fileid, path, size, parent FROM oc_filecache WHERE mimetype != 2"
    else:
        logger.info("Mode UPLOADS : nettoyage des chargements temporaires...")
        query = f"""
            SELECT f.fileid, f.path, f.size, f.parent FROM oc_filecache f
            JOIN oc_filecache p ON f.parent = p.fileid
            WHERE p.parent IN (SELECT fileid FROM oc_filecache WHERE path = 'uploads')
            AND p.storage_mtime < UNIX_TIMESTAMP(NOW() - INTERVAL {deletion_grace_period} SECOND)
        """

    cursor_read.execute(query)
    results = cursor_read.fetchall()
    logger.info(f"Fichiers à vérifier : {len(results)}")

    total_cleaned = 0
    size_recovered = 0
    parent_folders_to_check = set()

    for file in results:
        fileid = file['fileid']
        path = file['path']
        size = file['size'] or 0
        parent = file['parent']
        storage_filename = filename_pattern % fileid

        is_missing = False
        if args.scan_all:
            try:
                s3.head_object(Bucket=s3_bucket, Key=storage_filename)
            except s3.exceptions.ClientError as e:
                if e.response['Error']['Code'] == "404":
                    is_missing = True
                else:
                    logger.error(f"Erreur API S3 pour {storage_filename}: {e}")
                    continue
        else:
            is_missing = True

        if is_missing:
            msg_prefix = "[DRY-RUN] " if args.dry_run else ""
            logger.info(f" - {msg_prefix}Suppression : {storage_filename} ({path})")
            total_cleaned += 1
            size_recovered += size
            parent_folders_to_check.add(parent)

            if not args.dry_run:
                try:
                    if not args.scan_all:
                        try:
                            s3.delete_object(Bucket=s3_bucket, Key=storage_filename)
                        except: pass
                    cursor_write.execute("DELETE FROM `oc_filecache` WHERE `fileid` = %s", (fileid,))
                    db_write.commit()
                except Exception as e:
                    logger.error(f"Erreur suppression {fileid}: {e}")

    if not args.scan_all and not args.dry_run:
        for folder_id in parent_folders_to_check:
            cursor_write.execute("DELETE FROM `oc_filecache` WHERE `fileid` = %s", (folder_id,))
        db_write.commit()

    logger.info(f"Terminé. {total_cleaned} entrées nettoyées. {readable_bytes(size_recovered)} libérés.")

    cursor_read.close()
    cursor_write.close()
    db_read.close()
    db_write.close()

if __name__ == "__main__":
    main()