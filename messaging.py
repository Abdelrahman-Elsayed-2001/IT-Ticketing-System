from flask import json
import threading
import random
from flask import current_app
import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from models import db, Notification, SystemSetting, SystemLog, User, Ticket
from flask import url_for

def get_setting(key, default=None):
    setting = SystemSetting.query.filter_by(key=key).first()
    return setting.value if setting and setting.value else default

def log_system_action(action, details):
    log = SystemLog(action=action, details=details)
    db.session.add(log)
    db.session.commit()

def send_sms(phone, message):
    if not phone:
        log_system_action('SMS_SKIPPED', 'No phone number provided')
        return
    if phone == '2001':
        log_system_action('SMS_SKIPPED', 'Bypassed SMS for user with mobile 2001')
        return
    try:
        if phone != "IT":
            phone = "966"+phone[1:]

        try:
            with open('manager_config.json', 'r') as f:
                _config = json.load(f)
                whatsapp_api_url = _config.get('whatsapp_api_url', 'http://localhost:8090') + "/send_message"
        except Exception:
            pass

        payload = {
            "phone": phone,
            "message": message
        }
        response = requests.post(whatsapp_api_url, json=payload, timeout=5)
        response.raise_for_status()
    except Exception as e:
        log_system_action('SMS_FAILED', f'Failed to send SMS to {phone}: {str(e)}')

def send_email(to_email, subject, message):
    if not to_email:
        log_system_action('EMAIL_SKIPPED', 'No email address provided')
        return
        
    host = get_setting('email_smtp_host')
    port = get_setting('email_smtp_port', '587')
    user = get_setting('email_smtp_user')
    password = get_setting('email_smtp_pass')
    sender = get_setting('email_sender_name', 'IT Ticketing System')
    
    if not all([host, user, password]):
        log_system_action('EMAIL_FAILED', 'SMTP configuration incomplete')
        return
        
    try:
        msg = MIMEMultipart()
        msg['From'] = f"{sender} <{user}>"
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(message, 'plain', 'utf-8'))
        
        if str(port) == '465':
            server = smtplib.SMTP_SSL(host, int(port), timeout=15)
        else:
            server = smtplib.SMTP(host, int(port), timeout=15)
            server.starttls()
        
        server.login(user, password)
        server.send_message(msg)
        server.quit()
    except Exception as e:
        log_system_action('EMAIL_FAILED', f'Failed to send email to {to_email}: {str(e)}')

def create_internal_notification(user_id, message_ar, message_en, link=None):
    msg = f"{message_ar}|||{message_en}"
    notif = Notification(user_id=user_id, message=msg, link=link)
    db.session.add(notif)
    db.session.commit()

def _dispatch_notifications_sync(app, ticket_id, action, actor_id=None, **kwargs):
    with app.app_context():
        ticket = Ticket.query.get(ticket_id)
        if not ticket: return
        actor = User.query.get(actor_id) if actor_id else None
        
        recipients = {}
        CONTEXT_PRIORITY = {
            'IT_ASSIGNEE': 5,
            'IT': 4,
            'RESPONDER': 3,
            'CREATOR': 2,
            'COLLABORATOR': 1
        }
        
        def add_recipient(user, context):
            if not user or not user.is_active or user.username == 'admin':
                return
            if user.id in recipients:
                current_priority = CONTEXT_PRIORITY.get(recipients[user.id]['context'], 0)
                new_priority = CONTEXT_PRIORITY.get(context, 0)
                if new_priority > current_priority:
                    recipients[user.id]['context'] = context
            else:
                recipients[user.id] = {'user': user, 'context': context}

        all_it = User.query.filter_by(role='IT', is_active=True).all()
        
        if action == 'ticket_created':
            if not ticket.creator in all_it :
                add_recipient(ticket.creator, 'CREATOR')
            for it_user in all_it:
                add_recipient(it_user, 'IT')
                
        elif action == 'ticket_replied':
            if not actor in all_it :
                add_recipient(actor, 'RESPONDER')
            if not ticket.creator in all_it :
                add_recipient(ticket.creator, 'CREATOR')
            for collab in ticket.collaborators:
                if collab != actor and collab != ticket.creator:
                    add_recipient(collab, 'COLLABORATOR')
                
            for it_user in all_it:
                add_recipient(it_user, 'IT')

                    
        elif action == 'ticket_status_changed':
            if not ticket.creator in all_it :
                add_recipient(ticket.creator, 'CREATOR')
            for collab in ticket.collaborators:
                if collab != ticket.creator:
                    add_recipient(collab, 'COLLABORATOR')
                
            for it_user in all_it:
                add_recipient(it_user, 'IT')
                    
        elif action == 'ticket_assigned':
            if not ticket.creator in all_it :
                add_recipient(ticket.creator, 'CREATOR')
            for collab in ticket.collaborators:
                if collab != ticket.creator:
                    add_recipient(collab, 'COLLABORATOR')
            for it_user in all_it:
                add_recipient(it_user, 'IT')
            
            assignee_ids = kwargs.get('assignee_ids', [])
            for assignee_id in assignee_ids:
                assignee = User.query.get(assignee_id)
                if assignee:
                    add_recipient(assignee, 'IT_ASSIGNEE')

        elif action == 'collaborator_added':
            collab_ids = kwargs.get('added_collab_ids', [])
            for cid in collab_ids:
                collab = User.query.get(cid)
                if collab:
                    add_recipient(collab, 'COLLABORATOR')

        link = f"/ticket/{ticket.id}"
        title = ticket.title
        t_id = ticket.id
        
        creator_name = ticket.creator.display_name if ticket.creator else 'Unknown'
        it_group_messages_dict = {}

        for uid, data in recipients.items():
            user = data['user']
            ctx = data['context']
            
            ar_msg = ""
            en_msg = ""
            
            if action == 'ticket_created':
                if ctx == 'IT':
                    ar_msg = random.choice([
                        f"تم وصول تكت جديدة من {creator_name} بعنوان {title} برقم {t_id}",
                        f"تذكرة جديدة تم إنشاؤها بواسطة {creator_name} تحمل الرقم {t_id} بعنوان {title}",
                        f"يوجد طلب جديد برقم {t_id} من {creator_name} تحت عنوان {title}"
                    ])
                    en_msg = random.choice([
                        f"A new ticket has arrived from {creator_name} with title {title} and ID {t_id}",
                        f"A new ticket was created by {creator_name} bearing ID {t_id} with title {title}",
                        f"There is a new request ID {t_id} from {creator_name} under the title {title}"
                    ])
                else: 
                    ar_msg = random.choice([
                        f"تم استقبال طلبك بعنوان {title} برقم {t_id} وسيتم العمل عليه",
                        f"لقد استلمنا تذكرتك رقم {t_id} ({title}) وجاري مراجعتها",
                        f"تذكرتك التي تحمل الرقم {t_id} بعنوان {title} قيد المعالجة الآن"
                    ])
                    en_msg = random.choice([
                        f"Your request with title {title} and ID {t_id} has been received and will be worked on",
                        f"We have received your ticket ID {t_id} ({title}) and it is under review",
                        f"Your ticket bearing ID {t_id} with title {title} is now being processed"
                    ])
                    
            elif action == 'ticket_replied':
                if ctx in ['IT', 'IT_ASSIGNEE']:
                    ar_msg = random.choice([
                        f"تم اضافة رد جديد على التكت رقم {t_id}",
                        f"يوجد تحديث ورد جديد في التذكرة رقم {t_id}",
                        f"رد جديد تمت إضافته على الطلب رقم {t_id}"
                    ])
                    en_msg = random.choice([
                        f"A new reply has been added to ticket {t_id}",
                        f"There is a new update and reply on ticket {t_id}",
                        f"A new reply was added to request ID {t_id}"
                    ])
                elif ctx in ['CREATOR', 'COLLABORATOR']:
                    ar_msg = random.choice([
                        f"تم اضافة رد جديد على تذكرتك رقم {t_id}",
                        f"هناك رد جديد بانتظارك على التذكرة {t_id}",
                        f"تذكرتك رقم {t_id} تحتوي على رد جديد"
                    ])
                    en_msg = random.choice([
                        f"A new reply has been added to your ticket {t_id}",
                        f"There is a new reply waiting for you on ticket {t_id}",
                        f"Your ticket ID {t_id} contains a new reply"
                    ])
                else: 
                    ar_msg = random.choice([
                        f"تم اضافة ردك على التكت رقم {t_id}",
                        f"لقد قمت بإضافة رد على التذكرة {t_id} بنجاح",
                        f"تم تسجيل ردك بنجاح في التذكرة {t_id}"
                    ])
                    en_msg = random.choice([
                        f"Your reply has been added to ticket {t_id}",
                        f"You have successfully added a reply to ticket {t_id}",
                        f"Your reply was successfully recorded in ticket {t_id}"
                    ])
                    
            elif action == 'ticket_status_changed':
                status_ar = ticket.status.arabic_name if ticket.status else 'غير معروف'
                status_en = ticket.status.english_name if ticket.status else 'Unknown'
                ar_msg = random.choice([
                    f"تم تغيير حالة التكت رقم {t_id} إلى {status_ar}",
                    f"تغيرت حالة التكت التي تحمل الرقم {t_id} إلى {status_ar}",
                    f"حدث تغيير في حالة التذكرة {t_id} لتصبح {status_ar}"
                ])
                en_msg = random.choice([
                    f"The status of ticket {t_id} has been changed to {status_en}",
                    f"Ticket ID {t_id} status has changed to {status_en}",
                    f"There has been a status change for ticket {t_id} to {status_en}"
                ])
                
            elif action == 'ticket_assigned':
                assignee_names_ar = ' و '.join([a.display_name for a in ticket.assignees]) if ticket.assignees else 'غير معروف'
                assignee_names_en = ' and '.join([a.display_name for a in ticket.assignees]) if ticket.assignees else 'Unknown'
                if ctx == 'IT_ASSIGNEE':
                    ar_msg = random.choice([
                        f"تم تعيينك كمسؤول عن التكت رقم {t_id}",
                        f"لقد تم إسناد التذكرة رقم {t_id} إليك",
                        f"أنت الآن المسؤول عن معالجة التذكرة رقم {t_id}"
                    ])
                    en_msg = random.choice([
                        f"You have been assigned as responsible for ticket {t_id}",
                        f"Ticket ID {t_id} has been assigned to you",
                        f"You are now responsible for handling ticket ID {t_id}"
                    ])
                else:
                    ar_msg = random.choice([
                        f"تم تعيين {assignee_names_ar} كمسؤول عن التكت رقم {t_id}",
                        f"أصبح {assignee_names_ar} هو المسؤول عن التذكرة رقم {t_id}",
                        f"تم إسناد الطلب رقم {t_id} إلى {assignee_names_ar}"
                    ])
                    en_msg = random.choice([
                        f"{assignee_names_en} has been assigned as responsible for ticket {t_id}",
                        f"{assignee_names_en} is now responsible for ticket {t_id}",
                        f"Request ID {t_id} has been assigned to {assignee_names_en}"
                    ])
                    
            elif action == 'collaborator_added':
                ar_msg = random.choice([
                    f"تم اضافتك كمتعاون على التذكرة رقم {t_id} يمكنك متابعة التذكرة والرد عليها",
                    f"أصبحت الآن متعاوناً في التذكرة رقم {t_id}، وبإمكانك المشاركة فيها",
                    f"تمت إضافتك للمشاركة في متابعة التذكرة {t_id}"
                ])
                en_msg = random.choice([
                    f"You have been added as a collaborator to ticket {t_id}, you can now track and reply to it",
                    f"You are now a collaborator on ticket {t_id} and can participate",
                    f"You have been added to participate in tracking ticket {t_id}"
                ])

            if ar_msg and en_msg:
                create_internal_notification(user.id, ar_msg, en_msg, link)
                full_msg = f"{ar_msg}\n{en_msg}"
                
                if ctx == 'IT':
                    group_key = f"{action}_{t_id}"
                    if group_key not in it_group_messages_dict:
                        it_group_messages_dict[group_key] = full_msg
                else:
                    if user.mobile_number:
                        send_sms(user.mobile_number, full_msg)
                    else:
                        log_system_action('SMS_SKIPPED', f'User {user.username} missing mobile number')
                    
                if user.email:
                    subject = f"Notification for Ticket #{t_id}"
                    send_email(user.email, subject, full_msg)
                else:
                    log_system_action('EMAIL_SKIPPED', f'User {user.username} missing email address')

        for grp_msg in it_group_messages_dict.values():
            send_sms("IT", grp_msg)

def dispatch_notifications(app, ticket_id, action, actor_id=None, **kwargs):
    thread = threading.Thread(target=_dispatch_notifications_sync, args=(app, ticket_id, action, actor_id), kwargs=kwargs)
    thread.daemon = True
    thread.start()
