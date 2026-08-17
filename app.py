import os
from flask import Flask, request, g
from flask_bcrypt import Bcrypt
from flask_login import LoginManager
from models import db, User, Category, TicketStatus, SystemSetting
from translations import TRANSLATIONS
from backup_manager import init_scheduler
import json

app_version = "Unknown"
try:
    with open('manager_config.json', 'r') as f:
        _config = json.load(f)
        app_version = _config.get('version', 'Unknown')
except Exception:
    pass

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_secret_key_here'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///ticket.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.path.join(app.root_path, 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024 # 50 MB max per request just as a sane limit, though user said all sizes, let's allow large. Let's not set max, or set it to 500MB.
# app.config['MAX_CONTENT_LENGTH'] = None # Allow any size, but be careful in production. We'll leave it out.

# Ensure upload directory exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

db.init_app(app)
bcrypt = Bcrypt(app)
login_manager = LoginManager(app)
login_manager.login_view = 'main.login'
login_manager.login_message_category = 'info'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Import routes
from routes import main
app.register_blueprint(main)

@app.context_processor
def inject_translations():
    lang = request.cookies.get('lang', 'ar') # Default to Arabic
    def t(key):
        return TRANSLATIONS.get(lang, TRANSLATIONS['en']).get(key, key)
    return dict(t=t, current_lang=lang, app_version=app_version)

def init_db():
    with app.app_context():
        db.create_all()
        
        try:
            db.session.execute(db.text('ALTER TABLE user ADD COLUMN avaya_extension VARCHAR(50)'))
            db.session.commit()
        except Exception:
            db.session.rollback()

        try:
            db.session.execute(db.text('ALTER TABLE user ADD COLUMN is_mobile_public BOOLEAN DEFAULT 0'))
            db.session.commit()
        except Exception:
            db.session.rollback()

        try:
            db.session.execute(db.text('ALTER TABLE user ADD COLUMN contact_order INTEGER DEFAULT 0'))
            db.session.commit()
        except Exception:
            db.session.rollback()

        try:
            db.session.execute(db.text('ALTER TABLE ticket_status ADD COLUMN is_closed BOOLEAN DEFAULT 0'))
            db.session.commit()
        except Exception:
            db.session.rollback()

        try:
            db.session.execute(db.text('ALTER TABLE user ADD COLUMN arabic_display_name VARCHAR(150)'))
            db.session.commit()
        except Exception:
            db.session.rollback()

        try:
            db.session.execute(db.text('ALTER TABLE category ADD COLUMN is_pinned BOOLEAN DEFAULT 0'))
            db.session.commit()
        except Exception:
            db.session.rollback()

        try:
            db.session.execute(db.text('ALTER TABLE category ADD COLUMN manual_order INTEGER DEFAULT 0'))
            db.session.commit()
        except Exception:
            db.session.rollback()
            
        # Default Statuses
        if not TicketStatus.query.first():
            db.session.add_all([
                TicketStatus(arabic_name='مفتوح', english_name='Open', color='#28a745', is_closed=False), # type: ignore
                TicketStatus(arabic_name='قيد التقدم', english_name='In Progress', color='#ffc107', is_closed=False), # type: ignore
                TicketStatus(arabic_name='مغلق', english_name='Closed', color='#dc3545', is_closed=True) # type: ignore
            ])
            db.session.commit()
            print("Default statuses created.")

        # Default admin user if it doesn't exist
        admin = User.query.filter_by(username='admin').first()
        if not admin:
            hashed_pw = bcrypt.generate_password_hash('admin123').decode('utf-8')
            admin = User( # type: ignore
                username='admin',
                password_hash=hashed_pw,
                display_name='System Admin',
                department='IT',
                role='IT'
            )
            db.session.add(admin)
            db.session.commit()
            print("Default admin user created.")

        # Default Settings
        if not SystemSetting.query.first():
            db.session.add_all([
                SystemSetting(key='weekly_backup_day', value='fri', description='Day of the week for full backup'),
                SystemSetting(key='weekly_backup_time', value='00:00', description='Time for weekly full backup'),
                SystemSetting(key='daily_backup_time', value='02:00', description='Time for daily cumulative backup'),
                SystemSetting(key='backup_path', value=os.path.join(app.root_path, 'backups'), description='Path for saving backups')
            ])
            db.session.commit()
            print("Default settings created.")

        if not SystemSetting.query.filter_by(key='dashboard_popup_enabled').first():
            db.session.add_all([
                SystemSetting(key='dashboard_popup_enabled', value='false', description='Enable dashboard popup'),
                SystemSetting(key='dashboard_popup_message_ar', value='', description='Arabic popup message'),
                SystemSetting(key='dashboard_popup_message_en', value='', description='English popup message')
            ])
            db.session.commit()

if __name__ == '__main__':
    port = 5000
    debug=True
    host="127.0.0.1"
    init_db()
    init_scheduler(app)
    try:
        import json
        if os.path.exists('manager_config.json'):
            with open('manager_config.json', 'r') as f:
                config = json.load(f)
                port = int(config.get('port', 5000))
                debug=config.get('debug',True)
                host=config.get('host','127.0.0.1')
    except Exception:
        pass
    app.run(debug=debug, host=host, port=port)