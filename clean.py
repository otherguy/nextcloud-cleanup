import os
import math
import logging
import argparse
import boto3
import mysql.connector
from dotenv import load_dotenv

# Configure logging
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

def main():
    load_dotenv()

    parser = argparse.ArgumentParser(description='Nextcloud S3 Cleanup (Reliable Single-Threaded)')
    parser.add_argument('--dry-run', action='store_true', help='Simuler les suppressions')
    parser.add_argument('--scan-all', action='store_true', help='Vérifier l\'existence de TOUS les fichiers sur S3')
    args = parser.parse_args()

    # Configuration
    deletion_grace_period = int(os.getenv('DELETION_GRACE_PERIOD', 86400))
    filename_pattern = os.getenv('NEXTCLOUD_FILENAME_PATTERN', 'urn:oid:%d')
    s3_bucket = os.getenv('AWS_BUCKET')

    # Clients
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
    
    total_cleaned = 0
    size_recovered = 0
    parent_folders_to_check = set()

    logger.info("Analyse en cours...")
    
    # On utilise fetchall pour être sûr de tout avoir en mémoire avant de commencer les écritures
    results = cursor_read.fetchall()
    logger.info(f"Fichiers à vérifier : {len(results)}")

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
                    logger.error(f"Erreur API S3 (autre que 404) pour {storage_filename}: {e}")
                    continue
        else:
            is_missing = True # En mode upload, on veut supprimer de toute façon

        if is_missing:
            msg_prefix = "[DRY-RUN] " if args.dry_run else ""
            logger.info(f" - {msg_prefix}Suppression : {storage_filename} ({path})")
            
            total_cleaned += 1
            size_recovered += size
            parent_folders_to_check.add(parent)

            if not args.dry_run:
                try:
                    # Suppression S3 si mode uploads
                    if not args.scan_all:
                        try:
                            s3.delete_object(Bucket=s3_bucket, Key=storage_filename)
                        except: pass
                    
                    # Suppression BDD immédiate
                    cursor_write.execute("DELETE FROM `oc_filecache` WHERE `fileid` = %s", (fileid,))
                    db_write.commit()
                except Exception as e:
                    logger.error(f"Erreur suppression {fileid}: {e}")

    # Nettoyage des dossiers parents vides (seulement en mode uploads)
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
