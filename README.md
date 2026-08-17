🎫 IT Ticketing System
Project Description: A comprehensive web application built with Flask (Python), designed to manage and track IT support tickets within an organization. It provides a structured environment for employees to report technical issues and for the IT team to efficiently track, update, and resolve them.

✨ Key Features:

User & Role Management: Secure login system with distinct user roles (System Admin, IT Support Team, and Regular Users).
Bilingual Support: Seamlessly toggle between English and Arabic user interfaces.
Ticket Management: Create, categorize, and track the status of tickets (Open, In Progress, Closed).
File Attachments: Users can upload files and images with their tickets to better explain their issues.
Automated Backups: Scheduled automated database backups (daily and weekly) using APScheduler to prevent data loss.
Notifications & Messaging: Dashboard pop-ups and messaging system to communicate updates and announcements to users.
🛠️ Tech Stack:

Backend: Python 3, Flask
Database: SQLite (using Flask-SQLAlchemy)
Authentication & Security: Flask-Login, Flask-Bcrypt
Task Scheduling: APScheduler
Frontend: HTML, CSS, JavaScript (Jinja2 templating engine)

Initial login credentials are:
username: admin
password: admin123