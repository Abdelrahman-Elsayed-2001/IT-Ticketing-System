import os
import uuid
from datetime import datetime, timedelta
import pytz
from flask import Blueprint, render_template, redirect, url_for, flash, request, abort, current_app, send_from_directory, jsonify, make_response, session
from flask_login import login_user, current_user, logout_user, login_required
from werkzeug.utils import secure_filename
from sqlalchemy import or_, func
import csv
import io

import re
from models import db, User, Ticket, Reply, Category, TicketStatus, Attachment, Notification, SystemLog, SystemSetting, UserGroup, ChatMessage
from flask_bcrypt import check_password_hash, generate_password_hash
from messaging import dispatch_notifications

main = Blueprint('main', __name__)

def get_current_time():
    return datetime.now(pytz.timezone('Asia/Riyadh')).replace(tzinfo=None)

def log_action(user_id, action, target_type=None, target_id=None, details=None):
    log = SystemLog(
        user_id=user_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        details=details
    )
    db.session.add(log)
    db.session.commit()

def save_attachments(files, ticket_id=None, reply_id=None):
    if not files:
        return
    for file in files:
        if file.filename == '':
            continue
        
        file.seek(0, os.SEEK_END)
        file_size = file.tell()
        file.seek(0)
        
        max_size = current_app.config.get('MAX_CONTENT_LENGTH')
        if max_size and file_size > max_size:
            flash('File size is too large', 'error')
            return
        
        original_filename = file.filename
        ext = os.path.splitext(original_filename)[1]
        unique_filename = f"{get_current_time().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex}{ext}"
        filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], unique_filename)
        file.save(filepath)
        
        attachment = Attachment(
            filename=original_filename,
            filepath=unique_filename,
            ticket_id=ticket_id,
            reply_id=reply_id,
            uploaded_by=current_user.id
        )
        db.session.add(attachment)
    db.session.commit()

@main.context_processor
def inject_notifications():
    if current_user.is_authenticated:
        unread_notifs = Notification.query.filter_by(user_id=current_user.id, is_read=False).order_by(Notification.created_at.desc()).all()
        return dict(unread_notifications=unread_notifs)
    return dict(unread_notifications=[])

@main.before_request
def check_user_profile():
    if current_user.is_authenticated:
        if request.endpoint and request.endpoint not in ['main.logout', 'main.profile', 'static']:
            if not current_user.email and not current_user.mobile_number and not session.get('profile_skipped'):
                flash('Please update your profile with your email or mobile number to continue.', 'warning')
                return redirect(url_for('main.profile'))

@main.route('/set_lang/<lang>')
def set_lang(lang):
    if lang not in ['en', 'ar']:
        lang = 'ar'
    resp = make_response(redirect(request.referrer or url_for('main.dashboard')))
    resp.set_cookie('lang', lang, max_age=60*60*24*365) # 1 year
    return resp

@main.route('/notifications/read/<int:notif_id>', methods=['POST'])
@login_required
def mark_notification_read(notif_id):
    notif = Notification.query.get_or_404(notif_id)
    if notif.user_id == current_user.id:
        notif.is_read = True
        db.session.commit()
    return '', 204

@main.route('/notifications/read_all', methods=['POST'])
@login_required
def mark_all_notifications_read():
    notifs = Notification.query.filter_by(user_id=current_user.id, is_read=False).all()
    for notif in notifs:
        notif.is_read = True
    db.session.commit()
    return jsonify({'success': True})

@main.route('/', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter(func.lower(User.username) == func.lower(username)).first()
        if user and check_password_hash(user.password_hash, password):
            if not user.is_active:
                flash('Your account has been deactivated.', 'danger')
            else:
                login_user(user, remember=True)
                log_action(user.id, 'LOGIN', 'User', user.id, 'User logged in')
                return redirect(url_for('main.dashboard'))
        else:
            flash('Login Unsuccessful. Please check username and password', 'danger')
    return render_template('login.html')

@main.route('/logout')
def logout():
    if current_user.is_authenticated:
        log_action(current_user.id, 'LOGOUT', 'User', current_user.id, 'User logged out')
    
    session.pop('dashboard_popup_shown_date', None)
    logout_user()
    
    return redirect(url_for('main.login'))

@main.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'skip':
            session['profile_skipped'] = True
            return redirect(url_for('main.dashboard'))
            
        email = request.form.get('email')
        mobile_number = request.form.get('mobile_number')
        password = request.form.get('password')
        
        if not email and not mobile_number:
            flash('You must provide either an email or a mobile number.', 'danger')
        elif mobile_number and not re.match(r'^(05\d{8}|2001)$', mobile_number):
            flash('Mobile number must be 10 digits and start with 05 (or 2001).', 'danger')
        else:
            current_user.email = email
            current_user.mobile_number = mobile_number
            current_user.avaya_extension = request.form.get('avaya_extension')
            if current_user.role == 'IT':
                current_user.is_mobile_public = request.form.get('is_mobile_public') == 'on'
            if password:
                current_user.password_hash = generate_password_hash(password).decode('utf-8')
            db.session.commit()
            log_action(current_user.id, 'UPDATE_PROFILE', 'User', current_user.id, 'User updated profile details')
            flash('Profile updated successfully.', 'success')
            return redirect(url_for('main.dashboard'))
            
    return render_template('profile.html')

@main.route('/dashboard')
@login_required
def dashboard():
    if current_user.role == 'IT':
        tickets = Ticket.query.all()
    else:
        tickets = Ticket.query.filter(
            or_(Ticket.user_id == current_user.id, Ticket.collaborators.any(id=current_user.id))
        ).all()
        
    total_tickets = len(tickets)
    
    # Calculate closed tickets
    closed_tickets = [t for t in tickets if not t.is_archived and t.status and t.status.is_closed]
    closed_tickets_count = len(closed_tickets)
    
    # Calculate active tickets by subtracting archived and closed
    active_tickets = len([t for t in tickets if not t.is_archived and not (t.status and t.status.is_closed)])
    archived_tickets = total_tickets - active_tickets - closed_tickets_count
    
    # Status distribution for active tickets and status colors
    status_counts = {}
    status_colors = {}
    for t in tickets:
        if not t.is_archived and t.status:
            status_name = t.status.localized_name
            status_counts[status_name] = status_counts.get(status_name, 0) + 1
            status_colors[status_name] = t.status.color
            
    # Category distribution
    category_counts = {}
    for t in tickets:
        if not t.is_archived and t.category:
            cat_name = t.category.localized_name
            category_counts[cat_name] = category_counts.get(cat_name, 0) + 1
            
    # Assignee distribution for IT users
    assignee_counts = {}
    if current_user.role == 'IT':
        for t in tickets:
            if not t.is_archived and t.assignees:
                for a in t.assignees:
                    aname = a.localized_name
                    assignee_counts[aname] = assignee_counts.get(aname, 0) + 1
                    
    popup_enabled_setting = SystemSetting.query.filter_by(key='dashboard_popup_enabled').first()
    dashboard_popup_enabled = (popup_enabled_setting.value == 'true') if popup_enabled_setting else False
    
    show_dashboard_popup = False
    if dashboard_popup_enabled:
        today_str = get_current_time().strftime('%Y-%m-%d')
        if session.get('dashboard_popup_shown_date') != today_str:
            show_dashboard_popup = True
            session['dashboard_popup_shown_date'] = today_str
    
    popup_ar = SystemSetting.query.filter_by(key='dashboard_popup_message_ar').first()
    popup_en = SystemSetting.query.filter_by(key='dashboard_popup_message_en').first()
    dashboard_popup_message_ar = popup_ar.value if popup_ar else ''
    dashboard_popup_message_en = popup_en.value if popup_en else ''
            
    return render_template('dashboard.html', 
                           total_tickets=total_tickets, 
                           active_tickets_count=active_tickets, 
                           archived_tickets_count=archived_tickets,
                           closed_tickets_count=closed_tickets_count,
                           status_counts=status_counts,
                           status_colors=status_colors,
                           category_counts=category_counts,
                           assignee_counts=assignee_counts,
                           show_dashboard_popup=show_dashboard_popup,
                           dashboard_popup_message_ar=dashboard_popup_message_ar,
                           dashboard_popup_message_en=dashboard_popup_message_en)

@main.route('/tickets_list')
@login_required
def tickets_list():
    archived_tickets = []
    if current_user.role == 'IT':
        tickets = Ticket.query.filter_by(is_archived=False).order_by(Ticket.created_at.desc()).all()
        archived_tickets = Ticket.query.filter_by(is_archived=True).order_by(Ticket.created_at.desc()).all()
    else:
        tickets = Ticket.query.filter(
            Ticket.is_archived == False,
            or_(Ticket.user_id == current_user.id, Ticket.collaborators.any(id=current_user.id))
        ).order_by(Ticket.created_at.desc()).all()
        
    categories = Category.query.filter_by(is_active=True).all()
    statuses = TicketStatus.query.filter_by(is_active=True).all()
    all_users = User.query.filter_by(is_active=True).order_by(User.contact_order.asc(), User.id.asc()).all()
    
    return render_template('tickets_list.html', tickets=tickets, archived_tickets=archived_tickets, categories=categories, statuses=statuses, all_users=all_users)

@main.route('/export/<export_type>')
@login_required
def export_csv(export_type):
    si = io.StringIO()
    si.write('\ufeff') # Add BOM for Excel Arabic support
    cw = csv.writer(si)
    
    if export_type == 'tickets':
        if current_user.role == 'IT':
            tickets = Ticket.query.filter_by(is_archived=False).order_by(Ticket.created_at.desc()).all()
        else:
            tickets = Ticket.query.filter(
                Ticket.is_archived == False,
                or_(Ticket.user_id == current_user.id, Ticket.collaborators.any(id=current_user.id))
            ).order_by(Ticket.created_at.desc()).all()
            
        cw.writerow(['ID', 'Title', 'Category', 'Status','Opened by', 'Assignee', 'Date/Time', 'Collaborators', 'Description'])
        for t in tickets:
            assignees = ", ".join([a.display_name for a in t.assignees]) if t.assignees else "None"
            collabs = ", ".join([c.display_name for c in t.collaborators]) if t.collaborators else "None"
            category = t.category.english_name if t.category else "None"
            status = t.status.english_name if t.status else "None"
            cw.writerow([
                t.id, 
                t.title, 
                category, 
                status, 
                t.creator.username if t.creator else '-', 
                assignees, 
                t.created_at.strftime('%Y-%m-%d %H:%M:%S'), 
                collabs, 
                t.description
            ])
            
    elif export_type == 'users' and current_user.role == 'IT':
        users = User.query.all()
        cw.writerow(['ID', 'Username', 'Display Name en', 'Display Name ar', 'Email', 'Mobile', 'Role', 'Department', 'Active'])
        for u in users:
            cw.writerow([u.id, u.username, u.display_name, u.arabic_display_name, u.email, u.mobile_number, u.role, u.department, u.is_active])
            
    elif export_type == 'categories' and current_user.role == 'IT':
        categories = Category.query.all()
        cw.writerow(['ID', 'English Name', 'Arabic Name', 'Active'])
        for c in categories:
            cw.writerow([c.id, c.english_name, c.arabic_name, c.is_active])
            
    elif export_type == 'statuses' and current_user.role == 'IT':
        statuses = TicketStatus.query.all()
        cw.writerow(['ID', 'English Name', 'Arabic Name', 'Color', 'Active'])
        for s in statuses:
            cw.writerow([s.id, s.english_name, s.arabic_name, s.color, s.is_active])
            
    elif export_type == 'groups' and current_user.role == 'IT':
        groups = UserGroup.query.all()
        cw.writerow(['ID', 'Name', 'Description', 'Members Count', 'Active'])
        for g in groups:
            cw.writerow([g.id, g.name, g.description, len(g.users), g.is_active])
            
    elif export_type == 'notifications':
        notifs = Notification.query.filter_by(user_id=current_user.id).order_by(Notification.created_at.desc()).all()
        cw.writerow(['ID', 'Message', 'Read', 'Date/Time'])
        for n in notifs:
            msg = n.message.split('|||')
            cw.writerow([n.id, msg, n.is_read, n.created_at.strftime('%Y-%m-%d %H:%M:%S')])
            
    elif export_type == 'logs' and current_user.role == 'IT':
        system_logs = SystemLog.query.order_by(SystemLog.created_at.desc()).all()
        cw.writerow(['ID', 'Date/Time', 'User', 'Action', 'Target Type', 'Target ID', 'Details'])
        for log in system_logs:
            username = log.user.username if log.user else '-'
            cw.writerow([
                log.id,
                log.created_at.strftime('%Y-%m-%d %H:%M:%S'),
                username,
                log.action,
                log.target_type or '-',
                log.target_id or '-',
                log.details or '-'
            ])
            
    else:
        abort(403)
        
    output = make_response(si.getvalue().encode('utf-8'))
    output.headers["Content-Disposition"] = f"attachment; filename={export_type}_export.csv"
    output.headers["Content-type"] = "text/csv; charset=utf-8"
    return output

def get_ordered_categories():
    setting = SystemSetting.query.filter_by(key='category_sort_mode').first()
    mode = setting.value if setting else 'created'
    categories = Category.query.filter_by(is_active=True).all()
    
    if mode == 'manual':
        categories.sort(key=lambda c: (c.manual_order, c.id))
    elif mode == 'demand':
        ticket_counts = db.session.query(Ticket.category_id, func.count(Ticket.id)).group_by(Ticket.category_id).all()
        count_dict = dict(ticket_counts)
        categories.sort(key=lambda c: (not c.is_pinned, -count_dict.get(c.id, 0), c.id))
    else: # 'created'
        categories.sort(key=lambda c: (not c.is_pinned, c.id))
        
    return categories

@main.route('/ticket/new', methods=['GET', 'POST'])
@login_required
def new_ticket():
    categories = get_ordered_categories()
    statuses = TicketStatus.query.filter_by(is_active=True).all()
    all_users = User.query.filter_by(is_active=True).all()
    
    if request.method == 'POST':
        title = request.form.get('title')
        description = request.form.get('description')
        category_id = request.form.get('category_id')
        proxy_user_id = request.form.get('proxy_user_id')
        
        creator_id = current_user.id
        proxy_msg = ''
        if current_user.role == 'IT' and proxy_user_id:
            proxy_user = User.query.get(proxy_user_id)
            if proxy_user:
                creator_id = proxy_user_id
                proxy_msg = f' ({current_user.username} Opened Ticket by Proxy as {proxy_user.username})'
            
        if not title or not description or not category_id:
            flash('All fields are required', 'danger')
        elif not statuses:
            flash('System configuration error: No Ticket Statuses available.', 'danger')
        else:
            ticket = Ticket(
                title=title, 
                description=description, 
                user_id=creator_id, 
                category_id=category_id,
                status_id=statuses[0].id
            )
            db.session.add(ticket)
            db.session.commit()
            
            save_attachments(request.files.getlist('attachments'), ticket_id=ticket.id)
            
            log_action(current_user.id, 'CREATE_TICKET', 'Ticket', ticket.id, f'Title: {title}{proxy_msg}')
            dispatch_notifications(current_app._get_current_object(), ticket.id, 'ticket_created')
            flash('Ticket created successfully!', 'success')
            return redirect(url_for('main.dashboard'))
            
    return render_template('create_ticket.html', categories=categories, all_users=all_users)

@main.route('/ticket/<int:ticket_id>', methods=['GET', 'POST'])
@login_required
def ticket_detail(ticket_id):
    ticket = Ticket.query.get_or_404(ticket_id)
    
    if current_user.role != 'IT' and ticket.user_id != current_user.id and current_user not in ticket.collaborators:
        abort(403)
        
    it_users = User.query.filter(User.role == 'IT', User.is_active == True, User.username != 'admin').order_by(User.contact_order.asc(), User.id.asc()).all()
    staff_users = User.query.filter(User.is_active==True, User.role == 'Staff').all()
    statuses = TicketStatus.query.filter_by(is_active=True).all()
    categories = get_ordered_categories()
    groups = UserGroup.query.filter_by(is_active=True).all()
    
    now = get_current_time()
        
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'reply':
            content = request.form.get('content')
            if content:
                reply = Reply(content=content, ticket_id=ticket.id, user_id=current_user.id)
                db.session.add(reply)
                db.session.commit()
                save_attachments(request.files.getlist('attachments'), reply_id=reply.id)
                
                log_action(current_user.id, 'REPLY_TICKET', 'Reply', reply.id, f'Ticket ID: {ticket.id}')
                dispatch_notifications(current_app._get_current_object(), ticket.id, 'ticket_replied', actor_id=current_user.id)
                flash('Reply added!', 'success')
                
        elif action == 'update_status' and current_user.role == 'IT':
            status_id = request.form.get('status_id')
            if status_id:
                old_status = ticket.status.english_name if ticket.status else 'None'
                ticket.status_id = status_id
                db.session.commit()
                new_status = ticket.status.english_name
                log_action(current_user.id, 'CHANGE_STATE', 'Ticket', ticket.id, f'Changed from {old_status} to {new_status}')
                dispatch_notifications(current_app._get_current_object(), ticket.id, 'ticket_status_changed')
                flash('Status updated!', 'success')
                
        elif action == 'update_category' and current_user.role == 'IT':
            category_id = request.form.get('category_id')
            if category_id:
                old_cat = ticket.category.english_name if ticket.category else 'None'
                ticket.category_id = category_id
                db.session.commit()
                new_cat = ticket.category.english_name
                log_action(current_user.id, 'CHANGE_CATEGORY', 'Ticket', ticket.id, f'Changed from {old_cat} to {new_cat}')
                flash('Category updated!', 'success')
                
        elif action == 'assign' and current_user.role == 'IT':
            assignee_ids = request.form.getlist('assignee_ids')
            assignees = User.query.filter(User.id.in_(assignee_ids)).all() if assignee_ids else []
            ticket.assignees = assignees
            if assignees:
                ticket.assigned_to = assignees[0].id # fallback for legacy column
            else:
                ticket.assigned_to = None
            db.session.commit()
            
            if assignees:
                names = ", ".join([a.display_name for a in assignees])
                log_action(current_user.id, 'ASSIGN_TICKET', 'Ticket', ticket.id, f'Assigned to {names}')
                dispatch_notifications(current_app._get_current_object(), ticket.id, 'ticket_assigned', assignee_ids=assignee_ids)
                flash('Ticket assigned!', 'success')
            else:
                log_action(current_user.id, 'UNASSIGN_TICKET', 'Ticket', ticket.id, 'Removed all assignees')
                flash('All assignees removed!', 'success')
                
        elif action == 'add_collaborators':
            user_ids = request.form.getlist('user_ids')
            group_ids = request.form.getlist('group_ids') if current_user.role == 'IT' else []
            
            added_collabs = []
            # Add specific users
            for uid in user_ids:
                user = User.query.get(uid)
                if user and user not in ticket.collaborators and user.id != ticket.user_id:
                    ticket.collaborators.append(user)
                    added_collabs.append(user.id)
                    
            # Add users from groups
            for gid in group_ids:
                group = UserGroup.query.get(gid)
                if group:
                    for user in group.users:
                        if user not in ticket.collaborators and user.id != ticket.user_id:
                            ticket.collaborators.append(user)
                            added_collabs.append(user.id)
                            
            db.session.commit()
            if added_collabs:
                log_action(current_user.id, 'ADD_COLLABORATORS', 'Ticket', ticket.id, f'Added {len(added_collabs)} collaborators')
                dispatch_notifications(current_app._get_current_object(), ticket.id, 'collaborator_added', added_collab_ids=added_collabs)
                flash(f'Added {len(added_collabs)} collaborators successfully!', 'success')
            else:
                flash('No new collaborators were added.', 'info')
                
        elif action == 'remove_collaborator' and current_user.role == 'IT':
            collab_id = request.form.get('collab_id')
            user = User.query.get(collab_id)
            if user in ticket.collaborators:
                ticket.collaborators.remove(user)
                db.session.commit()
                log_action(current_user.id, 'REMOVE_COLLABORATOR', 'Ticket', ticket.id, f'Removed {user.display_name}')
                flash('Collaborator removed!', 'success')
                
        elif action == 'edit_ticket_content':
            delta = now - ticket.created_at
            if current_user.role == 'IT' or (ticket.user_id == current_user.id and delta.total_seconds() <= 900):
                new_desc = request.form.get('description')
                if new_desc:
                    ticket.description = new_desc
                    db.session.commit()
                    log_action(current_user.id, 'EDIT_TICKET', 'Ticket', ticket.id, 'Description updated')
                    flash('Ticket description updated.', 'success')
            else:
                flash('Edit period expired or permission denied.', 'danger')
                
        elif action == 'delete' and current_user.role == 'IT':
            ticket.is_archived = True
            db.session.commit()
            log_action(current_user.id, 'ARCHIVE_TICKET', 'Ticket', ticket.id, 'Ticket archived')
            flash('Ticket archived!', 'success')
            return redirect(url_for('main.dashboard'))
            
        elif action == 'restore' and current_user.role == 'IT':
            ticket.is_archived = False
            db.session.commit()
            log_action(current_user.id, 'RESTORE_TICKET', 'Ticket', ticket.id, 'Ticket restored')
            flash('Ticket restored!', 'success')
            
        return redirect(url_for('main.ticket_detail', ticket_id=ticket.id))
        
    def can_edit_or_delete(obj):
        if current_user.role == 'IT': return True
        if getattr(obj, 'user_id', getattr(obj, 'uploaded_by', None)) == current_user.id:
            delta = now - obj.created_at
            return delta.total_seconds() <= 900
        return False

    return render_template(
        'ticket_detail.html', 
        ticket=ticket, 
        it_users=it_users, 
        staff_users=staff_users,
        statuses=statuses,
        categories=categories,
        groups=groups,
        can_edit_or_delete=can_edit_or_delete
    )

@main.route('/ticket/<int:ticket_id>/replies', methods=['GET'])
@login_required
def get_ticket_replies(ticket_id):
    ticket = Ticket.query.get_or_404(ticket_id)
    if current_user.role != 'IT' and ticket.user_id != current_user.id and current_user not in ticket.collaborators:
        abort(403)
        
    now = get_current_time()
    def can_edit_or_delete(obj):
        if current_user.role == 'IT': return True
        if getattr(obj, 'user_id', getattr(obj, 'uploaded_by', None)) == current_user.id:
            delta = now - obj.created_at
            return delta.total_seconds() <= 900
        return False
        
    return render_template('_ticket_replies.html', ticket=ticket, can_edit_or_delete=can_edit_or_delete)

@main.route('/edit_reply/<int:reply_id>', methods=['POST'])
@login_required
def edit_reply(reply_id):
    reply = Reply.query.get_or_404(reply_id)
    now = get_current_time()
    delta = now - reply.created_at
    
    if current_user.role == 'IT' or (reply.user_id == current_user.id and delta.total_seconds() <= 900):
        new_content = request.form.get('content')
        if new_content:
            reply.content = new_content
            db.session.commit()
            log_action(current_user.id, 'EDIT_REPLY', 'Reply', reply.id, 'Reply updated')
            flash('Reply updated.', 'success')
    else:
        flash('Edit period expired or permission denied.', 'danger')
    return redirect(url_for('main.ticket_detail', ticket_id=reply.ticket_id))

@main.route('/delete_attachment/<int:attachment_id>', methods=['POST'])
@login_required
def delete_attachment(attachment_id):
    attachment = Attachment.query.get_or_404(attachment_id)
    now = get_current_time()
    delta = now - attachment.created_at
    
    if current_user.role == 'IT' or (attachment.uploaded_by == current_user.id and delta.total_seconds() <= 900):
        ticket_id = attachment.ticket.id if attachment.ticket else attachment.reply.ticket_id
        
        filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], attachment.filepath)
        if os.path.exists(filepath):
            os.remove(filepath)
            
        db.session.delete(attachment)
        db.session.commit()
        log_action(current_user.id, 'DELETE_ATTACHMENT', 'Attachment', attachment.id, 'Attachment deleted')
        flash('Attachment deleted.', 'success')
        return redirect(url_for('main.ticket_detail', ticket_id=ticket_id))
        
    flash('Delete period expired or permission denied.', 'danger')
    return redirect(request.referrer)

@main.route('/download/<int:attachment_id>')
@login_required
def download_attachment(attachment_id):
    attachment = Attachment.query.get_or_404(attachment_id)
    ticket = attachment.ticket if attachment.ticket else attachment.reply.ticket
    if current_user.role != 'IT' and ticket.user_id != current_user.id and current_user not in ticket.collaborators:
        abort(403)
    return send_from_directory(current_app.config['UPLOAD_FOLDER'], attachment.filepath, as_attachment=True, download_name=attachment.filename)

@main.route('/view_attachment/<int:attachment_id>')
@login_required
def view_attachment(attachment_id):
    attachment = Attachment.query.get_or_404(attachment_id)
    ticket = attachment.ticket if attachment.ticket else attachment.reply.ticket
    if current_user.role != 'IT' and ticket.user_id != current_user.id and current_user not in ticket.collaborators:
        abort(403)
        
    ext = attachment.filename.rsplit('.', 1)[1].lower() if '.' in attachment.filename else ''
    if ext in ['pdf', 'png', 'jpg', 'jpeg', 'gif', 'webp']:
        response = send_from_directory(current_app.config['UPLOAD_FOLDER'], attachment.filepath, as_attachment=False)
        # Prevent caching for inline view
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        return response
    else:
        return send_from_directory(current_app.config['UPLOAD_FOLDER'], attachment.filepath, as_attachment=True, download_name=attachment.filename)

@main.route('/logs', methods=['GET'])
@login_required
def logs():
    if current_user.role != 'IT':
        abort(403)
        
    user_filter = request.args.get('user_id')
    action_filter = request.args.get('action')
    
    query = SystemLog.query
    if user_filter:
        query = query.filter_by(user_id=user_filter)
    if action_filter:
        query = query.filter_by(action=action_filter)
        
    system_logs = query.order_by(SystemLog.created_at.desc()).limit(1000).all() # Limit to prevent overload
    
    users = User.query.all()
    actions = db.session.query(SystemLog.action).distinct().all()
    actions = [a[0] for a in actions]
    
    target_types = db.session.query(SystemLog.target_type).distinct().all()
    target_types = [t[0] for t in target_types if t[0]]
    
    return render_template('logs.html', logs=system_logs, users=users, actions=actions, target_types=target_types, selected_user=user_filter, selected_action=action_filter)

@main.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    if current_user.role != 'IT':
        abort(403)
        
    users = User.query.all()
    categories = Category.query.all()
    statuses = TicketStatus.query.all()
    groups = UserGroup.query.all()
    
    cat_mode_setting = SystemSetting.query.filter_by(key='category_sort_mode').first()
    category_sort_mode = cat_mode_setting.value if cat_mode_setting else 'created'
    
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'add_user':
            username = request.form.get('username')
            password = request.form.get('password')
            display_name = request.form.get('display_name')
            arabic_display_name = request.form.get('arabic_display_name')
            email = request.form.get('email')
            mobile_number = request.form.get('mobile_number')
            avaya_extension = request.form.get('avaya_extension')
            department = request.form.get('department')
            role = request.form.get('role')
            
            if User.query.filter_by(username=username).first():
                flash('Username already exists.', 'danger')
            elif mobile_number and not re.match(r'^(05\d{8}|2001)$', mobile_number):
                flash('Mobile number must be 10 digits and start with 05 (or 2001).', 'danger')
            else:
                hashed_pw = generate_password_hash(password).decode('utf-8')
                new_user = User(username=username, password_hash=hashed_pw, display_name=display_name, arabic_display_name=arabic_display_name, email=email, mobile_number=mobile_number, avaya_extension=avaya_extension, department=department, role=role)
                db.session.add(new_user)
                db.session.commit()
                log_action(current_user.id, 'CREATE_USER', 'User', new_user.id, f'User {username} created')
                flash('User added successfully!', 'success')
                
        elif action == 'toggle_user':
            user_id = request.form.get('user_id')
            user = User.query.get(user_id)
            if user.username == 'admin':
                flash('Cannot deactivate the main admin.', 'danger')
            else:
                user.is_active = not user.is_active
                db.session.commit()
                log_action(current_user.id, 'TOGGLE_USER', 'User', user.id, f'Active: {user.is_active}')
                flash(f"User {'activated' if user.is_active else 'deactivated'}.", 'success')
                
        elif action == 'add_category':
            arabic_name = request.form.get('arabic_name')
            english_name = request.form.get('english_name')
            if Category.query.filter((Category.arabic_name == arabic_name) | (Category.english_name == english_name)).first():
                flash('Category already exists.', 'danger')
            else:
                new_cat = Category(arabic_name=arabic_name, english_name=english_name)
                db.session.add(new_cat)
                db.session.commit()
                log_action(current_user.id, 'CREATE_CATEGORY', 'Category', new_cat.id, f'Category {english_name} created')
                flash('Category added successfully!', 'success')
                
        elif action == 'edit_category':
            cat_id = request.form.get('category_id')
            cat = Category.query.get(cat_id)
            if cat:
                cat.arabic_name = request.form.get('arabic_name')
                cat.english_name = request.form.get('english_name')
                db.session.commit()
                log_action(current_user.id, 'EDIT_CATEGORY', 'Category', cat.id, 'Category updated')
                flash('Category updated successfully!', 'success')
                
        elif action == 'toggle_category':
            cat_id = request.form.get('category_id')
            cat = Category.query.get(cat_id)
            cat.is_active = not cat.is_active
            db.session.commit()
            log_action(current_user.id, 'TOGGLE_CATEGORY', 'Category', cat.id, f'Active: {cat.is_active}')
            flash(f"Category {'activated' if cat.is_active else 'deactivated'}.", 'success')
            
        elif action == 'update_category_order_mode':
            mode = request.form.get('category_sort_mode')
            setting = SystemSetting.query.filter_by(key='category_sort_mode').first()
            if setting:
                setting.value = mode
            else:
                db.session.add(SystemSetting(key='category_sort_mode', value=mode, description='Mode for sorting categories'))
            db.session.commit()
            log_action(current_user.id, 'UPDATE_CATEGORY_SORT_MODE', 'SystemSetting', None, f'Category sort mode changed to {mode}')
            flash('Category sort mode updated!', 'success')
            
        elif action == 'update_category_order':
            cat_ids = request.form.getlist('category_ids')
            manual_orders = request.form.getlist('manual_orders')
            pinned_ids = request.form.getlist('pinned_ids')
            
            for cid, m_order in zip(cat_ids, manual_orders):
                cat = Category.query.get(cid)
                if cat:
                    try:
                        cat.manual_order = int(m_order)
                    except ValueError:
                        cat.manual_order = 0
                    cat.is_pinned = (str(cid) in pinned_ids)
            db.session.commit()
            log_action(current_user.id, 'UPDATE_CATEGORY_ORDER', 'Category', None, 'Category order/pins updated')
            flash('Category order and pins updated successfully!', 'success')
            
        elif action == 'add_status':
            arabic_name = request.form.get('arabic_name')
            english_name = request.form.get('english_name')
            color = request.form.get('color', '#6c757d')
            is_closed = request.form.get('is_closed') == 'on'
            if TicketStatus.query.filter((TicketStatus.arabic_name == arabic_name) | (TicketStatus.english_name == english_name)).first():
                flash('Status already exists.', 'danger')
            else:
                new_stat = TicketStatus(arabic_name=arabic_name, english_name=english_name, color=color, is_closed=is_closed)
                db.session.add(new_stat)
                db.session.commit()
                log_action(current_user.id, 'CREATE_STATUS', 'Status', new_stat.id, f'Status {english_name} created')
                flash('Status added successfully!', 'success')
                
        elif action == 'edit_status':
            stat_id = request.form.get('status_id')
            stat = TicketStatus.query.get(stat_id)
            if stat:
                stat.arabic_name = request.form.get('arabic_name')
                stat.english_name = request.form.get('english_name')
                stat.color = request.form.get('color', '#6c757d')
                stat.is_closed = request.form.get('is_closed') == 'on'
                db.session.commit()
                log_action(current_user.id, 'EDIT_STATUS', 'Status', stat.id, 'Status updated')
                flash('Status updated successfully!', 'success')
                
        elif action == 'toggle_status':
            stat_id = request.form.get('status_id')
            stat = TicketStatus.query.get(stat_id)
            stat.is_active = not stat.is_active
            db.session.commit()
            log_action(current_user.id, 'TOGGLE_STATUS', 'Status', stat.id, f'Active: {stat.is_active}')
            flash(f"Status {'activated' if stat.is_active else 'deactivated'}.", 'success')
            
        elif action == 'edit_user':
            user_id = request.form.get('user_id')
            user = User.query.get(user_id)
            mobile_number = request.form.get('mobile_number')
            if user:
                if mobile_number and not re.match(r'^(05\d{8}|2001)$', mobile_number):
                    flash('Mobile number must be 10 digits and start with 05 (or 2001).', 'danger')
                else:
                    user.display_name = request.form.get('display_name')
                    user.arabic_display_name = request.form.get('arabic_display_name')
                    user.email = request.form.get('email')
                    user.mobile_number = mobile_number
                    user.avaya_extension = request.form.get('avaya_extension')
                    user.department = request.form.get('department')
                    password = request.form.get('password')
                    if password:
                        user.password_hash = generate_password_hash(password).decode('utf-8')
                    db.session.commit()
                    log_action(current_user.id, 'EDIT_USER', 'User', user.id, 'User details/password updated')
                    flash('User updated successfully!', 'success')
                
        elif action == 'create_group':
            name = request.form.get('name')
            description = request.form.get('description')
            if UserGroup.query.filter_by(name=name).first():
                flash('Group name already exists.', 'danger')
            else:
                new_group = UserGroup(name=name, description=description)
                db.session.add(new_group)
                db.session.commit()
                log_action(current_user.id, 'CREATE_GROUP', 'UserGroup', new_group.id, f'Group {name} created')
                flash('Group created successfully!', 'success')
                
        elif action == 'toggle_group':
            group_id = request.form.get('group_id')
            group = UserGroup.query.get(group_id)
            if group:
                group.is_active = not group.is_active
                db.session.commit()
                log_action(current_user.id, 'TOGGLE_GROUP', 'UserGroup', group.id, f'Active: {group.is_active}')
                flash(f"Group {'activated' if group.is_active else 'deactivated'}.", 'success')
                
        elif action == 'update_group_members':
            group_id = request.form.get('group_id')
            group = UserGroup.query.get(group_id)
            if group:
                member_ids = request.form.getlist('members')
                group.users = User.query.filter(User.id.in_(member_ids)).all()
                db.session.commit()
                log_action(current_user.id, 'UPDATE_GROUP_MEMBERS', 'UserGroup', group.id, 'Members updated')
                flash('Group members updated successfully!', 'success')
                
        elif action == 'update_backup_settings':
            from backup_manager import update_scheduler_jobs
            weekly_day = request.form.get('weekly_backup_day')
            weekly_time = request.form.get('weekly_backup_time')
            daily_time = request.form.get('daily_backup_time')
            backup_path = request.form.get('backup_path')
            
            s_weekly_day = SystemSetting.query.filter_by(key='weekly_backup_day').first()
            s_weekly_time = SystemSetting.query.filter_by(key='weekly_backup_time').first()
            s_daily_time = SystemSetting.query.filter_by(key='daily_backup_time').first()
            s_backup_path = SystemSetting.query.filter_by(key='backup_path').first()
            
            if s_weekly_day: s_weekly_day.value = weekly_day
            if s_weekly_time: s_weekly_time.value = weekly_time
            if s_daily_time: s_daily_time.value = daily_time
            if s_backup_path:
                s_backup_path.value = backup_path
            else:
                db.session.add(SystemSetting(key='backup_path', value=backup_path, description='Path for saving backups'))
            
            db.session.commit()
            log_action(current_user.id, 'UPDATE_BACKUP_SETTINGS', 'SystemSetting', None, 'Backup settings updated')
            update_scheduler_jobs(current_app._get_current_object())
            flash('Backup settings updated and applied!', 'success')
            
        elif action == 'manual_backup':
            from backup_manager import perform_full_backup
            try:
                perform_full_backup()
                log_action(current_user.id, 'MANUAL_BACKUP', 'System', None, 'Manual full backup triggered successfully')
                flash('Backup created successfully!', 'success')
            except Exception as e:
                log_action(current_user.id, 'MANUAL_BACKUP_ERROR', 'System', None, str(e))
                flash(f'Backup failed: {str(e)}', 'danger')
                
        elif action == 'update_email_settings':
            keys = ['email_smtp_host', 'email_smtp_port', 'email_smtp_user', 'email_smtp_pass', 'email_sender_name']
            for k in keys:
                val = request.form.get(k)
                setting = SystemSetting.query.filter_by(key=k).first()
                if setting:
                    setting.value = val
                else:
                    db.session.add(SystemSetting(key=k, value=val))
            db.session.commit()
            log_action(current_user.id, 'UPDATE_EMAIL_SETTINGS', 'SystemSetting', None, 'Email settings updated')
            flash('Email settings updated successfully!', 'success')
            
        elif action == 'update_popup_settings':
            keys = ['dashboard_popup_enabled', 'dashboard_popup_message_ar', 'dashboard_popup_message_en']
            for k in keys:
                val = request.form.get(k)
                if k == 'dashboard_popup_enabled':
                    val = 'true' if val == 'on' else 'false'
                setting = SystemSetting.query.filter_by(key=k).first()
                if setting:
                    setting.value = val
                else:
                    db.session.add(SystemSetting(key=k, value=val))
            db.session.commit()
            log_action(current_user.id, 'UPDATE_POPUP_SETTINGS', 'SystemSetting', None, 'Dashboard popup settings updated')
            flash('Popup settings updated successfully!', 'success')
            
        elif action == 'update_it_order':
            user_ids = request.form.getlist('user_ids')
            orders = request.form.getlist('contact_orders')
            for uid, order in zip(user_ids, orders):
                u = User.query.get(uid)
                if u:
                    try:
                        u.contact_order = int(order)
                    except ValueError:
                        u.contact_order = 0
            db.session.commit()
            log_action(current_user.id, 'UPDATE_IT_ORDER', 'User', None, 'Updated IT contact order')
            flash('IT Contact Order updated successfully!', 'success')

        return redirect(url_for('main.settings'))
        
    # Get settings for template
    s_weekly_day = SystemSetting.query.filter_by(key='weekly_backup_day').first()
    s_weekly_time = SystemSetting.query.filter_by(key='weekly_backup_time').first()
    s_daily_time = SystemSetting.query.filter_by(key='daily_backup_time').first()
    s_backup_path = SystemSetting.query.filter_by(key='backup_path').first()
    default_backup_path = os.path.join(current_app.root_path, 'backups')
    
    email_settings = {k: SystemSetting.query.filter_by(key=k).first() for k in ['email_smtp_host', 'email_smtp_port', 'email_smtp_user', 'email_smtp_pass', 'email_sender_name']}
    email_settings_vals = {k: v.value if v else '' for k, v in email_settings.items()}
    
    popup_settings = {k: SystemSetting.query.filter_by(key=k).first() for k in ['dashboard_popup_enabled', 'dashboard_popup_message_ar', 'dashboard_popup_message_en']}
    popup_settings_vals = {k: v.value if v else '' for k, v in popup_settings.items()}
    
    it_users = User.query.filter(User.role == 'IT', User.is_active == True, User.username != 'admin').order_by(User.contact_order.asc(), User.id.asc()).all()
    
    return render_template('settings.html', users=users, categories=categories, statuses=statuses, groups=groups,
                           weekly_day=s_weekly_day.value if s_weekly_day else 'fri',
                           weekly_time=s_weekly_time.value if s_weekly_time else '00:00',
                           daily_time=s_daily_time.value if s_daily_time else '02:00',
                           backup_path=s_backup_path.value if s_backup_path else default_backup_path,
                           it_users=it_users,
                           category_sort_mode=category_sort_mode,
                           **email_settings_vals,
                           **popup_settings_vals)

@main.route('/notifications')
@login_required
def notifications():
    all_notifs = Notification.query.filter_by(user_id=current_user.id).order_by(Notification.created_at.desc()).all()
    return render_template('notifications.html', notifications=all_notifs)

@main.route('/contact')
@login_required
def contact_us():
    admin = User.query.filter_by(username='admin').first()
    it_users = User.query.filter(User.role == 'IT', User.is_active == True, User.username != 'admin').order_by(User.contact_order.asc(), User.id.asc()).all()
    return render_template('contact_us.html', admin=admin, it_users=it_users)

@main.route('/contact/<username>')
@login_required
def contact_it_user(username):
    it_user = User.query.filter_by(username=username, role='IT', is_active=True).first_or_404()
    return render_template('contact_it_user.html', it_user=it_user)

@main.route('/chat')
@login_required
def chat():
    from sqlalchemy import or_, and_
    # Get all messages where current_user is sender or receiver
    messages = ChatMessage.query.filter(
        or_(ChatMessage.sender_id == current_user.id, ChatMessage.receiver_id == current_user.id)
    ).all()
    
    chat_user_ids = set()
    unread_counts = {}
    for m in messages:
        if m.sender_id == current_user.id:
            chat_user_ids.add(m.receiver_id)
        else:
            chat_user_ids.add(m.sender_id)
            if not m.is_read:
                unread_counts[m.sender_id] = unread_counts.get(m.sender_id, 0) + 1
                
    active_chat_users = User.query.filter(User.id.in_(chat_user_ids), User.is_active == True).all()
    other_users = User.query.filter(User.id != current_user.id, User.id.notin_(chat_user_ids), User.is_active == True).all()
    
    selected_user_id = request.args.get('user_id', type=int)
    
    # If selected_user_id is in other_users, we might want to temporarily show them in the active list
    if selected_user_id and selected_user_id not in chat_user_ids:
        u = User.query.get(selected_user_id)
        if u and u.is_active:
            active_chat_users.insert(0, u)
            other_users = [user for user in other_users if user.id != selected_user_id]
            
    return render_template('chat.html', chat_users=active_chat_users, other_users=other_users, unread_counts=unread_counts, selected_user_id=selected_user_id)

@main.route('/api/chat/messages/<int:user_id>')
@login_required
def get_chat_messages(user_id):
    messages = ChatMessage.query.filter(
        or_(
            (ChatMessage.sender_id == current_user.id) & (ChatMessage.receiver_id == user_id),
            (ChatMessage.sender_id == user_id) & (ChatMessage.receiver_id == current_user.id)
        )
    ).order_by(ChatMessage.created_at.asc()).all()
    
    # Mark as read
    for msg in messages:
        if msg.receiver_id == current_user.id and not msg.is_read:
            msg.is_read = True
    db.session.commit()
    
    return jsonify([{
        'id': msg.id,
        'sender_id': msg.sender_id,
        'message': msg.message,
        'created_at': msg.created_at.strftime('%Y-%m-%d %H:%M:%S'),
        'is_me': msg.sender_id == current_user.id
    } for msg in messages])

@main.route('/api/chat/send', methods=['POST'])
@login_required
def send_chat_message():
    receiver_id = request.form.get('receiver_id', type=int)
    message_text = request.form.get('message')
    
    if receiver_id and message_text:
        msg = ChatMessage(sender_id=current_user.id, receiver_id=receiver_id, message=message_text)
        db.session.add(msg)
        
        # Add Notification
        notif = Notification(
            user_id=receiver_id, 
            message=f"New message from {current_user.display_name}|||New message from {current_user.display_name}",
            link=url_for('main.chat', user_id=current_user.id)
        )
        db.session.add(notif)
        db.session.commit()
        return jsonify({'status': 'success'})
    return jsonify({'status': 'error', 'message': 'Invalid data'})
