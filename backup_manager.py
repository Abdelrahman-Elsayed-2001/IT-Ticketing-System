import os
import shutil
from datetime import datetime
import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from flask import current_app

scheduler = BackgroundScheduler()

def perform_full_backup():
    """Weekly full backup of DB and uploads folder."""
    app = current_app._get_current_object()
    with app.app_context():
        # Get settings or default
        from models import SystemSetting
        
        path_setting = SystemSetting.query.filter_by(key='backup_path').first()
        base_backup_dir = path_setting.value if path_setting and path_setting.value else os.path.join(app.root_path, 'backups')
        
        timestamp = datetime.now(pytz.timezone('Asia/Riyadh')).strftime("%Y%m%d_%H%M%S")
        backup_dir = os.path.join(base_backup_dir, f'full_{timestamp}')
        os.makedirs(backup_dir, exist_ok=True)
        
        # Copy DB
        db_path = os.path.join(app.root_path, 'instance', 'ticket.db')
        if os.path.exists(db_path):
            shutil.copy2(db_path, os.path.join(backup_dir, 'ticket.db'))
            
        # Copy Uploads
        uploads_dir = app.config.get('UPLOAD_FOLDER', os.path.join(app.root_path, 'uploads'))
        backup_uploads_dir = os.path.join(backup_dir, 'uploads')
        if os.path.exists(uploads_dir):
            shutil.copytree(uploads_dir, backup_uploads_dir, dirs_exist_ok=True)
        
        print(f"Full backup completed at {backup_dir}")

def perform_cumulative_backup():
    """Daily cumulative backup of DB and synced uploads folder."""
    app = current_app._get_current_object()
    with app.app_context():
        from models import SystemSetting
        
        path_setting = SystemSetting.query.filter_by(key='backup_path').first()
        base_backup_dir = path_setting.value if path_setting and path_setting.value else os.path.join(app.root_path, 'backups')
        
        backup_dir = os.path.join(base_backup_dir, 'cumulative')
        os.makedirs(backup_dir, exist_ok=True)
        
        # Overwrite DB
        db_path = os.path.join(app.root_path, 'instance', 'ticket.db')
        if os.path.exists(db_path):
            shutil.copy2(db_path, os.path.join(backup_dir, 'ticket.db'))
            
        # Sync Uploads
        uploads_dir = app.config.get('UPLOAD_FOLDER', os.path.join(app.root_path, 'uploads'))
        backup_uploads_dir = os.path.join(backup_dir, 'uploads')
        os.makedirs(backup_uploads_dir, exist_ok=True)
        
        if os.path.exists(uploads_dir):
            # Copy new/updated files
            for item in os.listdir(uploads_dir):
                src_path = os.path.join(uploads_dir, item)
                dst_path = os.path.join(backup_uploads_dir, item)
                if os.path.isfile(src_path):
                    shutil.copy2(src_path, dst_path)
                    
            # Delete old files not in active uploads
            for item in os.listdir(backup_uploads_dir):
                src_path = os.path.join(uploads_dir, item)
                dst_path = os.path.join(backup_uploads_dir, item)
                if not os.path.exists(src_path):
                    if os.path.isfile(dst_path):
                        os.remove(dst_path)
                        
        print(f"Cumulative backup completed at {backup_dir}")

def update_scheduler_jobs(app):
    with app.app_context():
        from models import SystemSetting
        
        # Default settings if none
        weekly_day = SystemSetting.query.filter_by(key='weekly_backup_day').first()
        weekly_time = SystemSetting.query.filter_by(key='weekly_backup_time').first()
        daily_time = SystemSetting.query.filter_by(key='daily_backup_time').first()
        
        # Remove existing jobs
        for job in scheduler.get_jobs():
            job.remove()
            
        # Add Weekly Job
        if weekly_day and weekly_time and weekly_day.value and weekly_time.value:
            day_of_week = weekly_day.value.lower() # e.g. 'friday'
            # Convert python standard days '0-6' or 'mon-sun' to apscheduler compatible 'mon, tue, etc'
            # Assuming 'mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun' are saved in db
            hour, minute = weekly_time.value.split(':')
            scheduler.add_job(
                func=lambda: perform_full_backup_ctx(app),
                trigger='cron',
                day_of_week=day_of_week,
                hour=int(hour),
                minute=int(minute),
                id='weekly_full_backup'
            )
            
        # Add Daily Job
        if daily_time and daily_time.value:
            hour, minute = daily_time.value.split(':')
            scheduler.add_job(
                func=lambda: perform_cumulative_backup_ctx(app),
                trigger='cron',
                hour=int(hour),
                minute=int(minute),
                id='daily_cumulative_backup'
            )

def perform_full_backup_ctx(app):
    with app.app_context():
        perform_full_backup()

def perform_cumulative_backup_ctx(app):
    with app.app_context():
        perform_cumulative_backup()

def init_scheduler(app):
    if not scheduler.running:
        scheduler.start()
    update_scheduler_jobs(app)
