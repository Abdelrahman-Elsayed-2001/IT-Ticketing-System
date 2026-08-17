from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime
import pytz

db = SQLAlchemy()

def get_current_time():
    return datetime.now(pytz.timezone('Asia/Riyadh')).replace(tzinfo=None)

class BaseModel(db.Model):
    __abstract__ = True
    def __init__(self, **kwargs):
        super().__init__(**kwargs)


# Association table for Staff Collaborators
ticket_collaborators = db.Table('ticket_collaborators',
    db.Column('ticket_id', db.Integer, db.ForeignKey('ticket.id'), primary_key=True),
    db.Column('user_id', db.Integer, db.ForeignKey('user.id'), primary_key=True)
)

# Association table for User Groups
group_users = db.Table('group_users',
    db.Column('group_id', db.Integer, db.ForeignKey('user_group.id'), primary_key=True),
    db.Column('user_id', db.Integer, db.ForeignKey('user.id'), primary_key=True)
)

# Association table for Ticket Assignees
ticket_assignees = db.Table('ticket_assignees',
    db.Column('ticket_id', db.Integer, db.ForeignKey('ticket.id'), primary_key=True),
    db.Column('user_id', db.Integer, db.ForeignKey('user.id'), primary_key=True)
)

class UserGroup(BaseModel):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    description = db.Column(db.String(255), nullable=True)
    is_active = db.Column(db.Boolean, default=True)

class User(BaseModel, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(150), nullable=False)
    display_name = db.Column(db.String(150), nullable=False)
    arabic_display_name = db.Column(db.String(150), nullable=True)
    email = db.Column(db.String(150), nullable=True)
    mobile_number = db.Column(db.String(50), nullable=True)
    avaya_extension = db.Column(db.String(50), nullable=True)
    is_mobile_public = db.Column(db.Boolean, default=False)
    contact_order = db.Column(db.Integer, default=0)
    department = db.Column(db.String(150), nullable=True)
    role = db.Column(db.String(50), nullable=False, default='Staff') # 'IT' or 'Staff'
    is_active = db.Column(db.Boolean, default=True) # For soft delete

    @property
    def localized_name(self):
        from flask import request
        try:
            lang = request.cookies.get('lang', 'ar') if request else 'ar'
        except RuntimeError:
            lang = 'ar'
        if lang == 'ar' and self.arabic_display_name:
            return self.arabic_display_name
        return self.display_name


    # Relationships
    tickets_created = db.relationship('Ticket', backref='creator', lazy=True, foreign_keys='Ticket.user_id')
    tickets_assigned = db.relationship('Ticket', backref='assignee', lazy=True, foreign_keys='Ticket.assigned_to')
    replies = db.relationship('Reply', backref='author', lazy=True)
    notifications = db.relationship('Notification', backref='user', lazy=True, cascade='all, delete-orphan')
    groups = db.relationship('UserGroup', secondary=group_users, lazy='subquery', backref=db.backref('users', lazy=True))

class Category(BaseModel):
    id = db.Column(db.Integer, primary_key=True)
    arabic_name = db.Column(db.String(100), nullable=False)
    english_name = db.Column(db.String(100), nullable=False)
    is_pinned = db.Column(db.Boolean, default=False)
    manual_order = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True) # For soft delete
    
    tickets = db.relationship('Ticket', backref='category', lazy=True)

    @property
    def localized_name(self):
        from flask import request
        try:
            lang = request.cookies.get('lang', 'ar') if request else 'ar'
        except RuntimeError:
            lang = 'ar'
        return self.arabic_name if lang == 'ar' else self.english_name

class TicketStatus(BaseModel):
    id = db.Column(db.Integer, primary_key=True)
    arabic_name = db.Column(db.String(100), nullable=False)
    english_name = db.Column(db.String(100), nullable=False)
    color = db.Column(db.String(7), default='#6c757d')
    is_closed = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=True) # For soft delete

    tickets = db.relationship('Ticket', backref='status', lazy=True)

    @property
    def localized_name(self):
        from flask import request
        try:
            lang = request.cookies.get('lang', 'ar') if request else 'ar'
        except RuntimeError:
            lang = 'ar'
        return self.arabic_name if lang == 'ar' else self.english_name

class Ticket(BaseModel):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False)
    is_archived = db.Column(db.Boolean, default=False) # For soft delete
    created_at = db.Column(db.DateTime, default=get_current_time)
    updated_at = db.Column(db.DateTime, default=get_current_time, onupdate=get_current_time)
    
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    assigned_to = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'), nullable=False)
    status_id = db.Column(db.Integer, db.ForeignKey('ticket_status.id'), nullable=False)
    
    replies = db.relationship('Reply', backref='ticket', lazy=True, cascade='all, delete-orphan')
    collaborators = db.relationship('User', secondary=ticket_collaborators, lazy='subquery', backref=db.backref('shared_tickets', lazy=True))
    assignees = db.relationship('User', secondary='ticket_assignees', lazy='subquery',
        order_by='User.contact_order',
        backref=db.backref('assigned_tickets', lazy=True))
    attachments = db.relationship('Attachment', backref='ticket', lazy=True, cascade='all, delete-orphan', foreign_keys='Attachment.ticket_id')

class Reply(BaseModel):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=get_current_time)
    
    ticket_id = db.Column(db.Integer, db.ForeignKey('ticket.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    attachments = db.relationship('Attachment', backref='reply', lazy=True, cascade='all, delete-orphan', foreign_keys='Attachment.reply_id')

class Attachment(BaseModel):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    filepath = db.Column(db.String(500), nullable=False)
    created_at = db.Column(db.DateTime, default=get_current_time)
    
    ticket_id = db.Column(db.Integer, db.ForeignKey('ticket.id'), nullable=True)
    reply_id = db.Column(db.Integer, db.ForeignKey('reply.id'), nullable=True)
    uploaded_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    uploader = db.relationship('User', backref='attachments', lazy=True)

class Notification(BaseModel):
    id = db.Column(db.Integer, primary_key=True)
    message = db.Column(db.String(255), nullable=False)
    link = db.Column(db.String(255), nullable=True)
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=get_current_time)
    
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

class SystemLog(BaseModel):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True) # User who performed action
    action = db.Column(db.String(100), nullable=False)
    target_type = db.Column(db.String(50), nullable=True)
    target_id = db.Column(db.Integer, nullable=True)
    details = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=get_current_time)

    user = db.relationship('User', backref=db.backref('logs', lazy=True, cascade='all, delete-orphan'))

class SystemSetting(BaseModel):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.String(255), nullable=True)
    description = db.Column(db.String(255), nullable=True)

class ChatMessage(BaseModel):
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    message = db.Column(db.Text, nullable=False)
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=get_current_time)

    sender = db.relationship('User', foreign_keys=[sender_id], backref=db.backref('sent_messages', lazy=True, cascade='all, delete-orphan'))
    receiver = db.relationship('User', foreign_keys=[receiver_id], backref=db.backref('received_messages', lazy=True, cascade='all, delete-orphan'))
